"""Profile-aware stateful verifier and core metric computation."""

from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memoryflow import __version__
from memoryflow.constants import TTL_INF_MS
from memoryflow.diagnostics import Diagnostic, DiagnosticSeverity, has_errors
from memoryflow.events import EventType, MemoryFlowEvent
from memoryflow.metrics import MetricResult
from memoryflow.profiles import ConformanceProfile
from memoryflow.provenance import validate_provenance
from memoryflow.rational import Rational
from memoryflow.schema import ValidationReport, validate_jsonl_file
from memoryflow.status import ResultStatus
from memoryflow.verifier.model import (
    AuditCertificate,
    EntryLifecycle,
    EntryState,
    ObligationStatus,
    VerificationObligation,
)


@dataclass(frozen=True)
class AuditConfig:
    profile: ConformanceProfile = ConformanceProfile.P1
    uptake_horizon_ms: int | None = None
    correction_horizon_ms: int | None = None
    verify_deadline_ms: int | None = None
    risk_weight_map: dict[int, int] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.profile, ConformanceProfile):
            pass
        elif isinstance(self.profile, str):
            try:
                object.__setattr__(self, "profile", ConformanceProfile(self.profile))
            except ValueError as exc:
                raise ValueError("profile must be P0, P1, or P2") from exc
        elif not isinstance(self.profile, ConformanceProfile):
            raise ValueError("profile must be a ConformanceProfile")
        for field_name in (
            "uptake_horizon_ms",
            "correction_horizon_ms",
            "verify_deadline_ms",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a nonnegative integer or None")
            if field_name == "uptake_horizon_ms" and value == 0:
                raise ValueError("uptake_horizon_ms must be positive when configured")
        if self.risk_weight_map is None:
            return
        for risk_level, weight in self.risk_weight_map.items():
            if isinstance(risk_level, bool) or not isinstance(risk_level, int) or risk_level < 0:
                raise ValueError("risk_weight_map keys must be nonnegative integers")
            if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
                raise ValueError("risk_weight_map values must be nonnegative integers")


@dataclass
class _WriteRecord:
    entry_id: str
    update_id: str
    digest: str
    write_ms: int
    has_provenance: bool
    use_ms: int | None = None
    read_ms: int | None = None


@dataclass(frozen=True)
class _ObligatedUpdateRecord:
    entry_id: str
    update_id: str
    digest: str
    mutation_ms: int
    mutation_type: EventType
    has_provenance: bool


@dataclass
class _VerificationFailureRecord:
    entry_id: str
    update_id: str
    digest: str
    fail_ms: int
    correction_ms: int | None = None


@dataclass(frozen=True)
class _ReadSelectionPlan:
    selected_event_ids: set[int]
    diagnostics: list[Diagnostic]
    truncated_count: int


@dataclass
class _AuditRunState:
    config: AuditConfig
    entries: dict[str, EntryState]
    obligations: dict[tuple[str, str], VerificationObligation]
    update_bindings: dict[tuple[str, str], str]
    writes: list[_WriteRecord]
    write_lookup: dict[tuple[str, str], _WriteRecord]
    obligated_updates: list[_ObligatedUpdateRecord]
    failed_verifications: dict[tuple[str, str], list[_VerificationFailureRecord]]
    verification_failures: list[_VerificationFailureRecord]
    correction_latencies_ms: list[int]
    diagnostics: list[Diagnostic]
    counters: dict[str, int]
    obligation_deadline_heap: list[tuple[int, str, str]]
    boundary_heap: list[tuple[int, str, int]]
    boundary_epoch: dict[str, int]
    first_event_ms: int | None
    last_event_ms: int | None
    last_boundary_ms: int | None
    current_stale_exp: Rational
    current_risk_exp: Rational
    current_zombie_exp: Rational
    current_supersedence_exp: Rational
    stale_exp_int: Rational
    risk_exp_int: Rational
    zombie_exp_int: Rational
    supersedence_exp_int: Rational
    zombie_delays_ms: list[int]
    supersedence_delays_ms: list[int]
    selected_read_event_ids: set[int]


THEORY_SEMANTICS = [
    "telemetry_only_no_hidden_operation_inference",
    "deterministic_order_obs_time_collector_seq_event_id",
    "entry_state_machine_unknown_active_tombstoned_superseded",
    "profile_specific_fail_closed_or_downgrade",
    "version_binding_by_entry_id_update_id_and_content_digest",
]

DOWNGRADABLE_VALIDATION_ERROR_CODES = {
    "COLLECTOR_SEQ_NOT_STRICTLY_INCREASING",
    "ORDER_KEY_COLLISION",
}

P0_FAIL_CLOSED_WARNING_CODES = {
    "MISSING_EVENT_ID",
}


def audit_jsonl_file(path: Path | str, *, config: AuditConfig | None = None) -> AuditCertificate:
    audit_config = config or AuditConfig()
    source = Path(path)
    input_hash = _sha256_file(source)
    validation = validate_jsonl_file(
        source,
        allow_p2_read_without_version=audit_config.profile is ConformanceProfile.P2,
    )
    return audit_events(validation, config=audit_config, input_sha256=input_hash)


def audit_events(
    validation: ValidationReport,
    *,
    config: AuditConfig | None = None,
    input_sha256: str | None = None,
) -> AuditCertificate:
    audit_config = config or AuditConfig()
    run = _AuditRunState(
        config=audit_config,
        entries={},
        obligations={},
        update_bindings={},
        writes=[],
        write_lookup={},
        obligated_updates=[],
        failed_verifications={},
        verification_failures=[],
        correction_latencies_ms=[],
        diagnostics=[],
        counters={
            "events_processed": 0,
            "effective_writes": 0,
            "effective_deletes": 0,
            "effective_replaces": 0,
            "effective_corrections": 0,
            "implicit_creates": 0,
            "unknown_version_reads": 0,
            "zombie_references": 0,
            "superseded_references": 0,
            "verification_obligations": 0,
            "write_verification_obligations": 0,
            "correction_verification_obligations": 0,
            "verification_obligations_expired": 0,
            "verification_passes_satisfied": 0,
            "verification_failures": 0,
            "corrections_matched_to_failure": 0,
            "exposure_missing_state": 0,
            "read_truncated_by_cap": 0,
            "read_selection_invalid": 0,
        },
        obligation_deadline_heap=[],
        boundary_heap=[],
        boundary_epoch={},
        first_event_ms=None,
        last_event_ms=None,
        last_boundary_ms=None,
        current_stale_exp=Rational(0),
        current_risk_exp=Rational(0),
        current_zombie_exp=Rational(0),
        current_supersedence_exp=Rational(0),
        stale_exp_int=Rational(0),
        risk_exp_int=Rational(0),
        zombie_exp_int=Rational(0),
        supersedence_exp_int=Rational(0),
        zombie_delays_ms=[],
        supersedence_delays_ms=[],
        selected_read_event_ids=set(),
    )

    _apply_profile_validation_rules(validation, run)
    if _validation_forces_invalid(validation, audit_config.profile):
        return _certificate(
            validation=validation,
            run=run,
            input_sha256=input_sha256,
            forced_status=ResultStatus.INVALID,
        )

    read_selection = _build_read_selection_plan(validation.events, audit_config.profile)
    run.selected_read_event_ids = read_selection.selected_event_ids
    run.diagnostics.extend(read_selection.diagnostics)
    run.counters["read_truncated_by_cap"] = read_selection.truncated_count
    if read_selection.diagnostics:
        run.counters["read_selection_invalid"] = 1

    _check_obs_time_regression_by_collector_seq(validation.events, run)
    for event in validation.events:
        if run.first_event_ms is None:
            run.first_event_ms = event.envelope.obs_time_ms
        run.last_event_ms = event.envelope.obs_time_ms
        _expire_pending_obligations_before(run, event.envelope.obs_time_ms)
        _pop_stale_boundaries_up_to(run, event.envelope.obs_time_ms)
        _advance_exposure_to(run, event.envelope.obs_time_ms)
        run.counters["events_processed"] += 1
        _check_skew(event, run)
        _check_canonicalization(event, run)
        _process_event(event, run)
    if validation.events:
        end_ms = validation.events[-1].envelope.obs_time_ms
        _pop_stale_boundaries_up_to(run, end_ms)
        _advance_exposure_to(run, end_ms)
        _expire_pending_obligations_through(run, end_ms)

    return _certificate(validation=validation, run=run, input_sha256=input_sha256)


def _apply_profile_validation_rules(validation: ValidationReport, run: _AuditRunState) -> None:
    if run.config.profile is ConformanceProfile.P0 and validation.missing_event_id_count > 0:
        run.diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="P0_MISSING_EVENT_ID_FAIL_CLOSED",
                message=(
                    "P0 requires event_id for strict deduplication; missing event_id "
                    "prevents strict audit comparability"
                ),
                context={
                    "profile": run.config.profile.value,
                    "status": ResultStatus.INVALID.value,
                    "missing_event_id_count": validation.missing_event_id_count,
                },
            )
        )

    if run.config.profile is ConformanceProfile.P0:
        return

    for diagnostic in validation.diagnostics:
        if diagnostic.code not in DOWNGRADABLE_VALIDATION_ERROR_CODES:
            continue
        run.diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.WARNING,
                code=f"{diagnostic.code}_DOWNGRADED",
                message=(
                    "profile permits deterministic processing to continue, but strict "
                    "collector ordering comparability is downgraded"
                ),
                line=diagnostic.line,
                event_id=diagnostic.event_id,
                field=diagnostic.field,
                context={
                    "profile": run.config.profile.value,
                    "status": ResultStatus.DEGRADED.value,
                    "source_code": diagnostic.code,
                },
            )
        )


def _validation_forces_invalid(
    validation: ValidationReport, profile: ConformanceProfile
) -> bool:
    if profile is ConformanceProfile.P0:
        if validation.missing_event_id_count > 0:
            return True
        return has_errors(validation.diagnostics)
    return any(
        item.severity is DiagnosticSeverity.ERROR
        and item.code not in DOWNGRADABLE_VALIDATION_ERROR_CODES
        for item in validation.diagnostics
    )


def _build_read_selection_plan(
    events: list[MemoryFlowEvent],
    profile: ConformanceProfile,
) -> _ReadSelectionPlan:
    read_events = [event for event in events if event.event_type is EventType.MEM_READ]
    selected_ids = {id(event) for event in read_events}
    diagnostics: list[Diagnostic] = []
    truncated_count = 0
    by_request: dict[str, list[MemoryFlowEvent]] = {}
    for event in read_events:
        request_id = str(event.payload["request_id"])
        by_request.setdefault(request_id, []).append(event)

    for request_id, group in by_request.items():
        if not any("cap" in event.payload for event in group):
            continue
        group_diagnostics = _read_selection_group_diagnostics(
            request_id=request_id,
            group=group,
            profile=profile,
        )
        diagnostics.extend(group_diagnostics)
        if group_diagnostics:
            continue

        cap = int(group[0].payload["cap"])
        ordered = sorted(
            group,
            key=lambda event: (
                int(event.payload["rank"]),
                str(event.payload["entry_id"]),
                str(event.payload.get("update_id", "")),
                event.order_key,
            ),
        )
        selected_for_request = {id(event) for event in ordered[:cap]}
        truncated_count += len(group) - len(selected_for_request)
        for event in group:
            if id(event) not in selected_for_request:
                selected_ids.remove(id(event))

    return _ReadSelectionPlan(
        selected_event_ids=selected_ids,
        diagnostics=diagnostics,
        truncated_count=truncated_count,
    )


def _read_selection_group_diagnostics(
    *,
    request_id: str,
    group: list[MemoryFlowEvent],
    profile: ConformanceProfile,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    first_event = group[0]
    caps = [event.payload.get("cap") for event in group]
    if any(cap is None for cap in caps):
        diagnostics.append(
            _read_selection_diagnostic(
                profile,
                first_event,
                code="READ_CAP_MISSING_ON_GROUP_MEMBER",
                message="all MEM_READ events for a capped request_id must declare cap",
                request_id=request_id,
            )
        )
    elif len({int(cap) for cap in caps if isinstance(cap, int)}) != 1:
        diagnostics.append(
            _read_selection_diagnostic(
                profile,
                first_event,
                code="READ_CAP_INCONSISTENT",
                message="all MEM_READ events for a capped request_id must use the same cap",
                request_id=request_id,
            )
        )

    if any("rank" not in event.payload for event in group):
        diagnostics.append(
            _read_selection_diagnostic(
                profile,
                first_event,
                code="READ_CAP_MISSING_RANK",
                message="capped MEM_READ groups must include rank on every read",
                request_id=request_id,
            )
        )
    ranks = [
        (
            int(event.payload["rank"]),
            str(event.payload["entry_id"]),
            str(event.payload.get("update_id", "")),
        )
        for event in group
        if "rank" in event.payload
    ]
    if len(ranks) != len(set(ranks)):
        diagnostics.append(
            _read_selection_diagnostic(
                profile,
                first_event,
                code="READ_SELECTION_TIE",
                message="capped MEM_READ group has duplicate (rank, entry_id, update_id) keys",
                request_id=request_id,
            )
        )
    return diagnostics


def _read_selection_diagnostic(
    profile: ConformanceProfile,
    event: MemoryFlowEvent,
    *,
    code: str,
    message: str,
    request_id: str,
) -> Diagnostic:
    status = ResultStatus.INVALID if profile is ConformanceProfile.P0 else ResultStatus.DEGRADED
    severity = (
        DiagnosticSeverity.ERROR
        if profile is ConformanceProfile.P0
        else DiagnosticSeverity.WARNING
    )
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        line=event.line,
        event_id=event.event_id,
        context={
            "profile": profile.value,
            "status": status.value,
            "request_id": request_id,
        },
    )


def _process_event(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    match event.event_type:
        case EventType.MOS_DECLARE:
            _on_declare(event, run)
        case EventType.MEM_WRITE:
            _on_write(event, run, correction=False)
        case EventType.MEM_REPLACE:
            _on_replace(event, run)
        case EventType.MEM_DELETE:
            _on_delete(event, run)
        case EventType.MEM_READ | EventType.MEM_USE:
            _on_reference(event, run)
        case EventType.MEM_VERIFY:
            _on_verify(event, run)
        case EventType.MEM_CORRECT:
            _on_correct(event, run)


def _on_declare(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    entry_id = _required_str(event, "entry_id")
    entry = _entry(run, entry_id)
    if entry.state is EntryLifecycle.ACTIVE:
        _profile_issue(
            run,
            event,
            code="DECLARE_ACTIVE_ENTRY",
            message="MOS_DECLARE observed for an already Active entry",
            p0_error=True,
        )
        return
    elif entry.state in {EntryLifecycle.TOMBSTONED, EntryLifecycle.SUPERSEDED}:
        _profile_issue(
            run,
            event,
            code="DECLARE_TERMINAL_ENTRY",
            message="MOS_DECLARE cannot reactivate a Tombstoned or Superseded entry",
            p0_error=True,
        )
        return
    _remove_stale_contribution(run, entry)
    entry.state = EntryLifecycle.ACTIVE
    entry.delete_ms = None
    entry.sup_ms = None
    if "weight" in event.payload:
        entry.weight = Rational.from_json(event.payload["weight"], field_name="weight")
    if "ttl_ms" in event.payload:
        entry.ttl_ms = int(event.payload["ttl_ms"])
    if "risk_level" in event.payload:
        entry.risk_level = int(event.payload["risk_level"])
        _check_risk_weight_declared(run, event, entry.risk_level)
    if "content_digest" in event.payload:
        entry.current_digest = str(event.payload["content_digest"])
    if "update_id" in event.payload:
        entry.current_update_id = str(event.payload["update_id"])
    if entry.current_digest is not None and entry.current_update_id is not None:
        run.update_bindings[(entry_id, entry.current_update_id)] = entry.current_digest
    _schedule_stale_boundary(run, entry)


def _on_write(event: MemoryFlowEvent, run: _AuditRunState, *, correction: bool) -> None:
    entry_id = _required_str(event, "entry_id")
    entry = _entry(run, entry_id)
    if entry.state is EntryLifecycle.UNKNOWN:
        if run.config.profile is ConformanceProfile.P0:
            _profile_issue(
                run,
                event,
                code="P0_MISSING_DECLARE",
                message="P0 requires MOS_DECLARE before MEM_WRITE",
                p0_error=True,
            )
            return
        run.counters["implicit_creates"] += 1
        _profile_issue(
            run,
            event,
            code="IMPLICIT_CREATE_ON_WRITE",
            message="profile permits implicit creation on MEM_WRITE; comparability is downgraded",
            p0_error=False,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
    elif entry.state in {EntryLifecycle.TOMBSTONED, EntryLifecycle.SUPERSEDED}:
        _profile_issue(
            run,
            event,
            code="WRITE_TO_TERMINAL_ENTRY",
            message="MEM_WRITE cannot reactivate a Tombstoned or Superseded entry",
            p0_error=True,
        )
        return

    update_id = _required_str(event, "update_id")
    digest = _required_str(event, "content_digest")
    if not _check_update_binding_available(
        run,
        event,
        entry=entry,
        entry_id=entry_id,
        update_id=update_id,
        digest=digest,
    ):
        return
    if entry.current_update_id == update_id and entry.current_digest is not None:
        if entry.current_digest != digest:
            _profile_issue(
                run,
                event,
                code="UPDATE_ID_DIGEST_REBIND",
                message="same update_id is bound to a different content_digest",
                p0_error=True,
            )
            return
        _quality_observation(
            run,
            event,
            code="NO_OP_WRITE_IGNORED",
            message="MEM_WRITE repeats the current update_id/content_digest and is not effective",
        )
        return
    if entry.current_update_id is not None:
        _invalidate_pending_obligation(run, entry, event.envelope.obs_time_ms)
    _remove_stale_contribution(run, entry)
    entry.state = EntryLifecycle.ACTIVE
    entry.gen += 1
    key = (entry_id, update_id)
    has_provenance = _provenance_is_valid(event, run)
    run.obligations[key] = VerificationObligation(
        entry_id=entry_id,
        update_id=update_id,
        target_digest=digest,
        write_ms=event.envelope.obs_time_ms,
        gen=entry.gen,
        deadline_ms=_verification_deadline_ms(run, event.envelope.obs_time_ms),
        has_provenance=has_provenance,
    )
    _schedule_obligation_deadline(run, run.obligations[key])
    write_record = _WriteRecord(
        entry_id=entry_id,
        update_id=update_id,
        digest=digest,
        write_ms=event.envelope.obs_time_ms,
        has_provenance=has_provenance,
    )
    run.writes.append(write_record)
    run.write_lookup[key] = write_record
    run.obligated_updates.append(
        _ObligatedUpdateRecord(
            entry_id=entry_id,
            update_id=update_id,
            digest=digest,
            mutation_ms=event.envelope.obs_time_ms,
            mutation_type=event.event_type,
            has_provenance=has_provenance,
        )
    )
    run.update_bindings[key] = digest
    run.counters["verification_obligations"] += 1
    run.counters["write_verification_obligations"] += 1
    counter = "effective_corrections" if correction else "effective_writes"
    run.counters[counter] += 1
    entry.last_touch_ms = event.envelope.obs_time_ms
    entry.current_update_id = update_id
    entry.current_digest = digest
    entry.weight = Rational.from_json(event.payload["weight"], field_name="weight")
    entry.ttl_ms = int(event.payload["ttl_ms"])
    entry.risk_level = int(event.payload["risk_level"])
    _check_risk_weight_declared(run, event, entry.risk_level)
    entry.delete_ms = None
    entry.sup_ms = None
    _schedule_stale_boundary(run, entry)


def _on_replace(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    old_id = _required_str(event, "old_entry_id")
    new_id = _required_str(event, "new_entry_id")
    entry = _entry(run, old_id)
    new_entry = _entry(run, new_id)
    if entry.state is not EntryLifecycle.ACTIVE:
        _profile_issue(
            run,
            event,
            code="REPLACE_NON_ACTIVE_ENTRY",
            message="MEM_REPLACE is effective only from Active to Superseded",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return
    if "old_update_id" in event.payload and entry.current_update_id is not None:
        old_update_id = str(event.payload["old_update_id"])
        if old_update_id != entry.current_update_id:
            _profile_issue(
                run,
                event,
                code="REPLACE_OLD_UPDATE_MISMATCH",
                message="old_update_id does not match the current entry update_id",
                p0_error=True,
            )
            return
    if new_entry.state is EntryLifecycle.UNKNOWN:
        _profile_issue(
            run,
            event,
            code="REPLACE_TARGET_UNKNOWN_ENTRY",
            message="MEM_REPLACE new_entry_id is not declared or active",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return
    if new_entry.state is not EntryLifecycle.ACTIVE:
        _profile_issue(
            run,
            event,
            code="REPLACE_TARGET_NOT_ACTIVE",
            message="MEM_REPLACE new_entry_id must identify an Active replacement entry",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return
    if "new_update_id" in event.payload and new_entry.current_update_id is not None:
        new_update_id = str(event.payload["new_update_id"])
        if new_update_id != new_entry.current_update_id:
            _profile_issue(
                run,
                event,
                code="REPLACE_NEW_UPDATE_MISMATCH",
                message="new_update_id does not match the replacement entry update_id",
                p0_error=True,
            )
            return
    _remove_stale_contribution(run, entry)
    entry.state = EntryLifecycle.SUPERSEDED
    entry.sup_ms = event.envelope.obs_time_ms
    entry.delete_ms = None
    run.current_supersedence_exp += entry.weight or Rational(0)
    run.counters["effective_replaces"] += 1
    _invalidate_stale_boundary(run, entry.entry_id)


def _check_update_binding_available(
    run: _AuditRunState,
    event: MemoryFlowEvent,
    *,
    entry: EntryState,
    entry_id: str,
    update_id: str,
    digest: str,
) -> bool:
    key = (entry_id, update_id)
    existing_digest = run.update_bindings.get(key)
    if existing_digest is None:
        return True
    if existing_digest != digest:
        _profile_issue(
            run,
            event,
            code="UPDATE_ID_DIGEST_REBIND",
            message="same update_id is bound to a different content_digest",
            p0_error=True,
        )
        return False
    if entry.current_update_id == update_id:
        return True
    _profile_issue(
        run,
        event,
        code="UPDATE_ID_REUSE",
        message="update_id is reused for a non-current version within the same entry",
        p0_error=True,
    )
    return False


def _on_delete(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    entry_id = _required_str(event, "entry_id")
    entry = _entry(run, entry_id)
    if entry.state is EntryLifecycle.UNKNOWN:
        _profile_issue(
            run,
            event,
            code="DELETE_UNKNOWN_ENTRY",
            message="MEM_DELETE without known Active entry cannot be strict-audited",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return
    elif entry.state is not EntryLifecycle.ACTIVE:
        _profile_issue(
            run,
            event,
            code="DELETE_NON_ACTIVE_ENTRY",
            message="MEM_DELETE is effective only from Active to Tombstoned",
            p0_error=True,
        )
        return
    _remove_stale_contribution(run, entry)
    entry.state = EntryLifecycle.TOMBSTONED
    entry.delete_ms = event.envelope.obs_time_ms
    entry.sup_ms = None
    run.current_zombie_exp += entry.weight or Rational(0)
    run.counters["effective_deletes"] += 1
    _invalidate_stale_boundary(run, entry.entry_id)


def _on_reference(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    if event.event_type is EventType.MEM_READ and id(event) not in run.selected_read_event_ids:
        _quality_observation(
            run,
            event,
            code="READ_TRUNCATED_BY_CAP",
            message="MEM_READ is outside the deterministic first-cap selected set",
        )
        return

    entry_id = _required_str(event, "entry_id")
    entry = _entry(run, entry_id)
    if entry.state is EntryLifecycle.UNKNOWN:
        _profile_issue(
            run,
            event,
            code="REFERENCE_UNKNOWN_ENTRY",
            message=f"{event.event_type.value} references an Unknown entry",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return

    has_update = "update_id" in event.payload
    has_digest = "content_digest" in event.payload
    if not has_update or not has_digest:
        if run.config.profile is ConformanceProfile.P2 and event.event_type is EventType.MEM_READ:
            run.counters["unknown_version_reads"] += 1
            _profile_issue(
                run,
                event,
                code="P2_UNKNOWN_VERSION_READ",
                message="P2 treats MEM_READ without version binding as unknown-version",
                p0_error=False,
                best_effort=True,
            )
            _record_terminal_reference(event, run, entry)
            return
        _profile_issue(
            run,
            event,
            code="MISSING_VERSION_BINDING",
            message=f"{event.event_type.value} requires content_digest and update_id",
            p0_error=True,
        )
        return

    update_id = str(event.payload["update_id"])
    digest = str(event.payload["content_digest"])
    binding_valid = True
    if entry.current_update_id is None or entry.current_digest is None:
        _profile_issue(
            run,
            event,
            code="REFERENCE_TARGET_VERSION_UNKNOWN",
            message=(
                f"{event.event_type.value} carries version fields, but the entry has no "
                "declared current update_id/content_digest to bind against"
            ),
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        binding_valid = False
    if entry.current_update_id is not None and update_id != entry.current_update_id:
        _profile_issue(
            run,
            event,
            code="REFERENCE_UPDATE_MISMATCH",
            message="reference update_id does not match the current entry update_id",
            p0_error=True,
        )
        binding_valid = False
    if entry.current_digest is not None and digest != entry.current_digest:
        _profile_issue(
            run,
            event,
            code="REFERENCE_DIGEST_MISMATCH",
            message="reference content_digest does not match the current entry digest",
            p0_error=True,
        )
        binding_valid = False
    if binding_valid:
        _mark_write_reference(run, event, entry_id=entry_id, update_id=update_id)
    _record_terminal_reference(event, run, entry)


def _record_terminal_reference(
    event: MemoryFlowEvent,
    run: _AuditRunState,
    entry: EntryState,
) -> None:
    if (
        entry.state is EntryLifecycle.TOMBSTONED
        and entry.delete_ms is not None
        and event.envelope.obs_time_ms > entry.delete_ms
    ):
        delay_ms = event.envelope.obs_time_ms - entry.delete_ms
        run.zombie_delays_ms.append(delay_ms)
        run.counters["zombie_references"] += 1
        _quality_observation(
            run,
            event,
            code="ZOMBIE_REFERENCE_OBSERVED",
            message=f"{event.event_type.value} references a Tombstoned entry",
            context={"delay_ms": delay_ms},
        )
    elif (
        entry.state is EntryLifecycle.SUPERSEDED
        and entry.sup_ms is not None
        and event.envelope.obs_time_ms > entry.sup_ms
    ):
        delay_ms = event.envelope.obs_time_ms - entry.sup_ms
        run.supersedence_delays_ms.append(delay_ms)
        run.counters["superseded_references"] += 1
        _quality_observation(
            run,
            event,
            code="SUPERSEDED_REFERENCE_OBSERVED",
            message=f"{event.event_type.value} references a Superseded entry",
            context={"delay_ms": delay_ms},
        )


def _on_verify(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    entry_id = _required_str(event, "target_entry_id")
    update_id = _required_str(event, "target_update_id")
    digest = _required_str(event, "target_digest")
    verdict = _required_str(event, "verdict")
    obligation = run.obligations.get((entry_id, update_id))
    if obligation is None:
        _profile_issue(
            run,
            event,
            code="VERIFY_WITHOUT_OBLIGATION",
            message="MEM_VERIFY does not match a known effective MEM_WRITE/MEM_CORRECT obligation",
            p0_error=True,
        )
        return
    if obligation.target_digest != digest:
        _profile_issue(
            run,
            event,
            code="VERIFY_DIGEST_MISMATCH",
            message="MEM_VERIFY target_digest does not match the obligation digest",
            p0_error=True,
        )
        return
    if verdict == "FAIL":
        _record_verification_failure(
            run,
            entry_id=entry_id,
            update_id=update_id,
            digest=digest,
            fail_ms=event.envelope.obs_time_ms,
        )
        return
    if obligation.status is not ObligationStatus.PENDING:
        if obligation.status is ObligationStatus.EXPIRED and verdict == "PASS":
            _profile_issue(
                run,
                event,
                code="VERIFY_PASS_AFTER_DEADLINE",
                message="MEM_VERIFY PASS occurred after verify_deadline_ms",
                p0_error=True,
                context={
                    "deadline_ms": obligation.deadline_ms,
                    "verify_ms": event.envelope.obs_time_ms,
                },
            )
            return
        _quality_observation(
            run,
            event,
            code="VERIFY_AFTER_OBLIGATION_CLOSED",
            message="MEM_VERIFY cannot satisfy an obligation closed by a later mutation",
            context={"obligation_status": obligation.status.value},
        )
        return
    if (
        obligation.deadline_ms is not None
        and event.envelope.obs_time_ms > obligation.deadline_ms
    ):
        _profile_issue(
            run,
            event,
            code="VERIFY_PASS_AFTER_DEADLINE",
            message="MEM_VERIFY PASS occurred after verify_deadline_ms",
            p0_error=True,
            context={
                "deadline_ms": obligation.deadline_ms,
                "verify_ms": event.envelope.obs_time_ms,
            },
        )
        return
    obligation.status = ObligationStatus.SATISFIED
    obligation.satisfied_ms = event.envelope.obs_time_ms
    run.counters["verification_passes_satisfied"] += 1


def _on_correct(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    entry_id = _required_str(event, "target_entry_id")
    entry = _entry(run, entry_id)
    target_update_id = _required_str(event, "target_update_id")
    target_digest = _required_str(event, "target_digest")
    corrected_update_id = _required_str(event, "corrected_update_id")
    corrected_digest = _required_str(event, "corrected_digest")

    if entry.state is EntryLifecycle.UNKNOWN:
        _profile_issue(
            run,
            event,
            code="CORRECT_UNKNOWN_ENTRY",
            message="MEM_CORRECT cannot implicitly create an Unknown entry",
            p0_error=True,
            best_effort=run.config.profile is ConformanceProfile.P2,
        )
        return
    if not _check_update_binding_available(
        run,
        event,
        entry=entry,
        entry_id=entry_id,
        update_id=corrected_update_id,
        digest=corrected_digest,
    ):
        return
    if entry.current_update_id == corrected_update_id and entry.current_digest is not None:
        if entry.current_digest != corrected_digest:
            _profile_issue(
                run,
                event,
                code="UPDATE_ID_DIGEST_REBIND",
                message="same corrected_update_id is bound to a different corrected_digest",
                p0_error=True,
            )
            return
        _quality_observation(
            run,
            event,
            code="NO_OP_CORRECTION_IGNORED",
            message=(
                "MEM_CORRECT repeats the current update_id/content_digest and is not "
                "an effective correction"
            ),
        )
        return

    _record_correction_latency(
        run,
        entry_id=entry_id,
        target_update_id=target_update_id,
        target_digest=target_digest,
        correction_ms=event.envelope.obs_time_ms,
    )

    if entry.state in {EntryLifecycle.TOMBSTONED, EntryLifecycle.SUPERSEDED}:
        _profile_issue(
            run,
            event,
            code="CORRECT_TERMINAL_ENTRY",
            message="MEM_CORRECT cannot reactivate a Tombstoned or Superseded entry",
            p0_error=True,
        )
        return

    if entry.current_update_id is not None and target_update_id != entry.current_update_id:
        _profile_issue(
            run,
            event,
            code="CORRECT_UPDATE_MISMATCH",
            message="MEM_CORRECT target_update_id does not match the current entry update_id",
            p0_error=True,
        )
        return
    if entry.current_digest is not None and target_digest != entry.current_digest:
        _profile_issue(
            run,
            event,
            code="CORRECT_DIGEST_MISMATCH",
            message="MEM_CORRECT target_digest does not match the current entry digest",
            p0_error=True,
        )
        return

    if entry.current_update_id is not None:
        _invalidate_pending_obligation(run, entry, event.envelope.obs_time_ms)
    _remove_stale_contribution(run, entry)
    entry.state = EntryLifecycle.ACTIVE
    entry.gen += 1
    has_provenance = _provenance_is_valid(event, run)
    run.obligations[(entry_id, corrected_update_id)] = VerificationObligation(
        entry_id=entry_id,
        update_id=corrected_update_id,
        target_digest=corrected_digest,
        write_ms=event.envelope.obs_time_ms,
        gen=entry.gen,
        deadline_ms=_verification_deadline_ms(run, event.envelope.obs_time_ms),
        has_provenance=has_provenance,
    )
    _schedule_obligation_deadline(run, run.obligations[(entry_id, corrected_update_id)])
    run.obligated_updates.append(
        _ObligatedUpdateRecord(
            entry_id=entry_id,
            update_id=corrected_update_id,
            digest=corrected_digest,
            mutation_ms=event.envelope.obs_time_ms,
            mutation_type=event.event_type,
            has_provenance=has_provenance,
        )
    )
    run.update_bindings[(entry_id, corrected_update_id)] = corrected_digest
    run.counters["verification_obligations"] += 1
    run.counters["correction_verification_obligations"] += 1
    run.counters["effective_corrections"] += 1
    entry.last_touch_ms = event.envelope.obs_time_ms
    entry.current_update_id = corrected_update_id
    entry.current_digest = corrected_digest
    if "corrected_weight" in event.payload:
        entry.weight = Rational.from_json(
            event.payload["corrected_weight"],
            field_name="corrected_weight",
        )
    if "corrected_ttl_ms" in event.payload:
        entry.ttl_ms = int(event.payload["corrected_ttl_ms"])
    if "corrected_risk_level" in event.payload:
        entry.risk_level = int(event.payload["corrected_risk_level"])
        _check_risk_weight_declared(run, event, entry.risk_level)
    entry.delete_ms = None
    entry.sup_ms = None
    _schedule_stale_boundary(run, entry)


def _mark_write_reference(
    run: _AuditRunState,
    event: MemoryFlowEvent,
    *,
    entry_id: str,
    update_id: str,
) -> None:
    record = run.write_lookup.get((entry_id, update_id))
    if record is None:
        return
    reference_ms = event.envelope.obs_time_ms
    if event.event_type is EventType.MEM_USE:
        if record.use_ms is None or reference_ms < record.use_ms:
            record.use_ms = reference_ms
    elif event.event_type is EventType.MEM_READ:
        if record.read_ms is None or reference_ms < record.read_ms:
            record.read_ms = reference_ms


def _record_correction_latency(
    run: _AuditRunState,
    *,
    entry_id: str,
    target_update_id: str,
    target_digest: str,
    correction_ms: int,
) -> None:
    failures = run.failed_verifications.get((entry_id, target_update_id))
    if not failures:
        return
    for index, failure in enumerate(failures):
        if failure.digest != target_digest:
            continue
        matched = failures.pop(index)
        matched.correction_ms = correction_ms
        run.correction_latencies_ms.append(correction_ms - matched.fail_ms)
        run.counters["corrections_matched_to_failure"] += 1
        return


def _record_verification_failure(
    run: _AuditRunState,
    *,
    entry_id: str,
    update_id: str,
    digest: str,
    fail_ms: int,
) -> None:
    key = (entry_id, update_id)
    failure = _VerificationFailureRecord(
        entry_id=entry_id,
        update_id=update_id,
        digest=digest,
        fail_ms=fail_ms,
    )
    run.failed_verifications.setdefault(key, []).append(failure)
    run.verification_failures.append(failure)
    run.counters["verification_failures"] += 1


def _schedule_stale_boundary(run: _AuditRunState, entry: EntryState) -> None:
    _invalidate_stale_boundary(run, entry.entry_id)
    if entry.state is not EntryLifecycle.ACTIVE:
        return
    if entry.last_touch_ms is None or entry.ttl_ms is None:
        return
    if entry.ttl_ms < 0:
        return
    boundary_ms = entry.last_touch_ms + entry.ttl_ms
    run.boundary_epoch[entry.entry_id] = run.boundary_epoch.get(entry.entry_id, 0) + 1
    heapq.heappush(
        run.boundary_heap,
        (boundary_ms, entry.entry_id, run.boundary_epoch[entry.entry_id]),
    )


def _invalidate_stale_boundary(run: _AuditRunState, entry_id: str) -> None:
    run.boundary_epoch[entry_id] = run.boundary_epoch.get(entry_id, 0) + 1


def _pop_stale_boundaries_up_to(run: _AuditRunState, t_ms: int) -> None:
    while run.boundary_heap and run.boundary_heap[0][0] <= t_ms:
        boundary_ms, entry_id, tag = heapq.heappop(run.boundary_heap)
        if run.boundary_epoch.get(entry_id) != tag:
            continue
        _advance_exposure_to(run, boundary_ms)
        entry = run.entries.get(entry_id)
        if entry is not None:
            _add_stale_contribution(run, entry)


def _advance_exposure_to(run: _AuditRunState, boundary_ms: int) -> None:
    if run.last_boundary_ms is None:
        run.last_boundary_ms = boundary_ms
        return
    if boundary_ms <= run.last_boundary_ms:
        return
    dt = boundary_ms - run.last_boundary_ms
    run.stale_exp_int += run.current_stale_exp * dt
    run.risk_exp_int += run.current_risk_exp * dt
    run.zombie_exp_int += run.current_zombie_exp * dt
    run.supersedence_exp_int += run.current_supersedence_exp * dt
    run.last_boundary_ms = boundary_ms


def _add_stale_contribution(run: _AuditRunState, entry: EntryState) -> None:
    if entry.stale_integral_active:
        return
    if entry.state is not EntryLifecycle.ACTIVE:
        return
    if not _entry_is_stale_for_open_interval(entry, run.last_boundary_ms or 0):
        return
    weight = entry.weight or Rational(0)
    run.current_stale_exp += weight
    run.current_risk_exp += weight * _risk_weight(run, entry.risk_level)
    entry.stale_integral_active = True


def _remove_stale_contribution(run: _AuditRunState, entry: EntryState) -> None:
    if not entry.stale_integral_active:
        return
    weight = entry.weight or Rational(0)
    run.current_stale_exp -= weight
    run.current_risk_exp -= weight * _risk_weight(run, entry.risk_level)
    entry.stale_integral_active = False


def _risk_weight(run: _AuditRunState, risk_level: int | None) -> int:
    if risk_level is None:
        return 0
    if run.config.risk_weight_map is None:
        return max(0, risk_level)
    return max(0, run.config.risk_weight_map.get(risk_level, 0))


def _check_risk_weight_declared(
    run: _AuditRunState,
    event: MemoryFlowEvent,
    risk_level: int,
) -> None:
    if run.config.risk_weight_map is None or risk_level in run.config.risk_weight_map:
        return
    _profile_issue(
        run,
        event,
        code="RISK_WEIGHT_UNDECLARED",
        message="risk_level is absent from the declared risk_weight_map",
        p0_error=True,
        context={"risk_level": risk_level},
    )


def _compute_report_instant_exposures(
    run: _AuditRunState,
    t_ms: int,
) -> tuple[Rational, Rational, Rational, Rational]:
    stale_exp = Rational(0)
    risk_exp = Rational(0)
    zombie_exp = Rational(0)
    supersedence_exp = Rational(0)
    for entry in run.entries.values():
        if entry.state is EntryLifecycle.ACTIVE:
            if _entry_is_stale_at_time(entry, t_ms):
                stale_exp += entry.weight or Rational(0)
                risk_exp += (entry.weight or Rational(0)) * _risk_weight(run, entry.risk_level)
        elif entry.state is EntryLifecycle.TOMBSTONED:
            zombie_exp += entry.weight or Rational(0)
        elif entry.state is EntryLifecycle.SUPERSEDED:
            supersedence_exp += entry.weight or Rational(0)
    return stale_exp, risk_exp, zombie_exp, supersedence_exp


def _entry_is_stale_for_open_interval(entry: EntryState, t_ms: int) -> bool:
    if entry.last_touch_ms is None or entry.ttl_ms is None:
        return False
    if entry.ttl_ms >= TTL_INF_MS:
        return False
    return t_ms >= entry.last_touch_ms + entry.ttl_ms


def _entry_is_stale_at_time(entry: EntryState, t_ms: int) -> bool:
    if entry.last_touch_ms is None or entry.ttl_ms is None:
        return False
    if entry.ttl_ms >= TTL_INF_MS:
        return False
    return t_ms > entry.last_touch_ms + entry.ttl_ms


def _entry(run: _AuditRunState, entry_id: str) -> EntryState:
    entry = run.entries.get(entry_id)
    if entry is None:
        entry = EntryState(entry_id=entry_id)
        run.entries[entry_id] = entry
    return entry


def _required_str(event: MemoryFlowEvent, field: str) -> str:
    return str(event.payload[field])


def _invalidate_pending_obligation(
    run: _AuditRunState, entry: EntryState, invalidated_ms: int
) -> None:
    if entry.current_update_id is None:
        return
    obligation = run.obligations.get((entry.entry_id, entry.current_update_id))
    if obligation is None or obligation.status is not ObligationStatus.PENDING:
        return
    obligation.status = ObligationStatus.INVALIDATED_BY_MUTATION
    obligation.invalidated_ms = invalidated_ms


def _expire_pending_obligations_before(run: _AuditRunState, t_ms: int) -> None:
    _expire_pending_obligations(run, t_ms=t_ms, include_boundary=False)


def _expire_pending_obligations_through(run: _AuditRunState, t_ms: int) -> None:
    _expire_pending_obligations(run, t_ms=t_ms, include_boundary=True)


def _expire_pending_obligations(
    run: _AuditRunState,
    *,
    t_ms: int,
    include_boundary: bool,
) -> None:
    while run.obligation_deadline_heap:
        deadline_ms, entry_id, update_id = run.obligation_deadline_heap[0]
        expired = deadline_ms <= t_ms if include_boundary else deadline_ms < t_ms
        if not expired:
            return
        heapq.heappop(run.obligation_deadline_heap)
        obligation = run.obligations.get((entry_id, update_id))
        if obligation is None:
            continue
        if obligation.status is not ObligationStatus.PENDING:
            continue
        if obligation.deadline_ms != deadline_ms:
            continue
        obligation.status = ObligationStatus.EXPIRED
        obligation.expired_ms = deadline_ms
        run.counters["verification_obligations_expired"] += 1


def _schedule_obligation_deadline(
    run: _AuditRunState,
    obligation: VerificationObligation,
) -> None:
    if obligation.deadline_ms is None:
        return
    heapq.heappush(
        run.obligation_deadline_heap,
        (obligation.deadline_ms, obligation.entry_id, obligation.update_id),
    )


def _verification_deadline_ms(run: _AuditRunState, write_ms: int) -> int | None:
    if run.config.verify_deadline_ms is None:
        return None
    return write_ms + run.config.verify_deadline_ms


def _provenance_is_valid(event: MemoryFlowEvent, run: _AuditRunState) -> bool:
    if "provenance" not in event.payload:
        return False
    result = validate_provenance(event.payload["provenance"])
    if result.valid:
        return True
    _profile_issue(
        run,
        event,
        code="INVALID_PROVENANCE",
        message=result.reason or "provenance object failed declared structure checks",
        p0_error=False,
    )
    return False


def _check_skew(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    event_time_ms = event.envelope.event_time_ms
    if event_time_ms is None:
        return
    delta_ms = abs(event_time_ms - event.envelope.obs_time_ms)
    if delta_ms <= event.envelope.skew_budget_ms:
        return
    _profile_issue(
        run,
        event,
        code="SKEW_BUDGET_EXCEEDED",
        message="event_time differs from obs_time beyond skew_budget_ms",
        p0_error=True,
        context={"delta_ms": delta_ms, "skew_budget_ms": event.envelope.skew_budget_ms},
    )


def _check_canonicalization(event: MemoryFlowEvent, run: _AuditRunState) -> None:
    if event.raw.get("canon_alg") != "none":
        return
    if not any(
        field in event.raw
        for field in ("content_digest", "target_digest", "corrected_digest")
    ):
        return
    _profile_issue(
        run,
        event,
        code="NON_CANONICAL_DIGEST_DECLARED",
        message=(
            'canon_alg="none" declares non-canonical hashing; digest-sensitive '
            "comparability is unavailable under strict audit"
        ),
        p0_error=True,
    )


def _check_obs_time_regression_by_collector_seq(
    events: list[MemoryFlowEvent], run: _AuditRunState
) -> None:
    by_collector = sorted(
        events, key=lambda item: (item.envelope.collector_id, item.envelope.collector_seq)
    )
    last_by_collector: dict[str, MemoryFlowEvent] = {}
    for event in by_collector:
        previous = last_by_collector.get(event.envelope.collector_id)
        if previous is not None:
            regression = previous.envelope.obs_time_ms - event.envelope.obs_time_ms
            if regression > event.envelope.skew_budget_ms:
                _profile_issue(
                    run,
                    event,
                    code="COLLECTOR_OBS_TIME_REGRESSION",
                    message=(
                        "collector obs_time regresses by more than skew_budget_ms "
                        "when events are inspected by collector_seq"
                    ),
                    p0_error=True,
                    context={
                        "previous_line": previous.line,
                        "previous_obs_time_ms": previous.envelope.obs_time_ms,
                        "current_obs_time_ms": event.envelope.obs_time_ms,
                        "regression_ms": regression,
                    },
                )
        last_by_collector[event.envelope.collector_id] = event


def _profile_issue(
    run: _AuditRunState,
    event: MemoryFlowEvent,
    *,
    code: str,
    message: str,
    p0_error: bool,
    best_effort: bool = False,
    context: dict[str, Any] | None = None,
) -> bool:
    severity = DiagnosticSeverity.WARNING
    status = ResultStatus.BEST_EFFORT if best_effort else ResultStatus.DEGRADED
    if run.config.profile is ConformanceProfile.P0 and p0_error:
        severity = DiagnosticSeverity.ERROR
        status = ResultStatus.INVALID
    run.diagnostics.append(
        Diagnostic(
            severity=severity,
            code=code,
            message=message,
            line=event.line,
            event_id=event.event_id,
            context={
                "profile": run.config.profile.value,
                "status": status.value,
                **(context or {}),
            },
        )
    )
    return severity is not DiagnosticSeverity.ERROR


def _quality_observation(
    run: _AuditRunState,
    event: MemoryFlowEvent,
    *,
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    run.diagnostics.append(
        Diagnostic(
            severity=DiagnosticSeverity.INFO,
            code=code,
            message=message,
            line=event.line,
            event_id=event.event_id,
            context={
                "profile": run.config.profile.value,
                "status": ResultStatus.VALID.value,
                **(context or {}),
            },
        )
    )


def _build_metrics(run: _AuditRunState, certificate_status: ResultStatus) -> list[MetricResult]:
    metric_status = _metric_status_from_certificate(certificate_status)
    first_ms = run.last_boundary_ms
    # last_boundary_ms has been advanced to the final audit boundary when events exist.
    metrics: list[MetricResult] = []
    metrics.append(_churn_metric(run, metric_status))
    metrics.append(_uptake_metric(run, metric_status, use_based=True))
    metrics.append(_uptake_metric(run, metric_status, use_based=False))
    metrics.append(_correction_latency_metric(run, metric_status))
    metrics.append(_fraction_uncorrected_metric(run, metric_status))
    metrics.append(_vuf_metric(run, metric_status))
    metrics.append(_puf_metric(run, metric_status))
    metrics.extend(_exposure_metrics(run, metric_status))
    if first_ms is None and certificate_status is not ResultStatus.INVALID:
        # Keep the branch explicit: empty streams are valid but most metrics are not computable.
        return [
            metric
            if metric.name == "churn_rate"
            else MetricResult(
                name=metric.name,
                status=ResultStatus.NOT_COMPUTABLE,
                value=None,
                unit=metric.unit,
                reasons=["empty event stream"],
            )
            for metric in metrics
        ]
    return metrics


def _metric_status_from_certificate(status: ResultStatus) -> ResultStatus:
    if status is ResultStatus.INVALID:
        return ResultStatus.INVALID
    if status is ResultStatus.BEST_EFFORT:
        return ResultStatus.BEST_EFFORT
    if status is ResultStatus.DEGRADED:
        return ResultStatus.DEGRADED
    return ResultStatus.VALID


def _churn_metric(
    run: _AuditRunState,
    metric_status: ResultStatus,
) -> MetricResult:
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name="churn_rate",
            status=ResultStatus.INVALID,
            value=None,
            unit="effective_mutations_per_ms",
            reasons=["audit status is INVALID"],
        )
    if run.first_event_ms is None or run.last_event_ms is None:
        return MetricResult(
            name="churn_rate",
            status=ResultStatus.NOT_COMPUTABLE,
            value=None,
            unit="effective_mutations_per_ms",
            reasons=["audit window duration is zero or unknown"],
        )
    duration_ms = run.last_event_ms - run.first_event_ms
    if duration_ms <= 0:
        return MetricResult(
            name="churn_rate",
            status=ResultStatus.NOT_COMPUTABLE,
            value=None,
            unit="effective_mutations_per_ms",
            reasons=["audit window duration is zero"],
        )
    mutations = (
        run.counters["effective_writes"]
        + run.counters["effective_deletes"]
        + run.counters["effective_replaces"]
        + run.counters["effective_corrections"]
    )
    return MetricResult(
        name="churn_rate",
        status=metric_status,
        value=Rational(mutations, duration_ms).to_json(),
        unit="effective_mutations_per_ms",
    )


def _uptake_metric(
    run: _AuditRunState,
    metric_status: ResultStatus,
    *,
    use_based: bool,
) -> MetricResult:
    name = "uptake" if use_based else "read_uptake"
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name=name,
            status=ResultStatus.INVALID,
            reasons=["audit status is INVALID"],
        )
    if run.config.uptake_horizon_ms is None:
        return MetricResult(
            name=name,
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["uptake_horizon_ms is not configured"],
        )
    denominator = len(run.writes)
    if denominator == 0:
        return MetricResult(
            name=name,
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["no effective MEM_WRITE events"],
        )
    horizon = run.config.uptake_horizon_ms
    count = 0
    for record in run.writes:
        reference_ms = record.use_ms if use_based else record.read_ms
        if reference_ms is not None and record.write_ms < reference_ms <= record.write_ms + horizon:
            count += 1
    status = metric_status
    reasons: list[str] = []
    if not use_based and run.counters["unknown_version_reads"] > 0:
        status = ResultStatus.NONCOMPARABLE
        reasons.append(
            "unknown-version reads cannot support comparable version-sensitive read uptake"
        )
    return MetricResult(
        name=name,
        status=status,
        value=Rational(count, denominator).to_json(),
        reasons=reasons,
    )


def _correction_latency_metric(
    run: _AuditRunState,
    metric_status: ResultStatus,
) -> MetricResult:
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name="correction_latency",
            status=ResultStatus.INVALID,
            value=None,
            unit="ms",
            reasons=["audit status is INVALID"],
        )
    return MetricResult(
        name="correction_latency",
        status=metric_status,
        value=sorted(run.correction_latencies_ms),
        unit="ms",
    )


def _fraction_uncorrected_metric(
    run: _AuditRunState,
    metric_status: ResultStatus,
) -> MetricResult:
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name="fraction_uncorrected_within_horizon",
            status=ResultStatus.INVALID,
            reasons=["audit status is INVALID"],
        )
    if run.config.correction_horizon_ms is None:
        return MetricResult(
            name="fraction_uncorrected_within_horizon",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["correction_horizon_ms is not configured"],
        )
    if run.last_event_ms is None:
        return MetricResult(
            name="fraction_uncorrected_within_horizon",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["audit window end is unknown"],
        )
    horizon = run.config.correction_horizon_ms
    matured_failures = [
        failure
        for failure in run.verification_failures
        if failure.fail_ms + horizon <= run.last_event_ms
    ]
    if not matured_failures:
        return MetricResult(
            name="fraction_uncorrected_within_horizon",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["no MEM_VERIFY FAIL events have matured past correction_horizon_ms"],
        )
    uncorrected = 0
    for failure in matured_failures:
        if failure.correction_ms is None or failure.correction_ms - failure.fail_ms > horizon:
            uncorrected += 1
    return MetricResult(
        name="fraction_uncorrected_within_horizon",
        status=metric_status,
        value=Rational(uncorrected, len(matured_failures)).to_json(),
    )


def _vuf_metric(run: _AuditRunState, metric_status: ResultStatus) -> MetricResult:
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name="vuf",
            status=ResultStatus.INVALID,
            reasons=["audit status is INVALID"],
        )
    if run.config.verify_deadline_ms is None:
        return MetricResult(
            name="vuf",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["verify_deadline_ms is not configured"],
        )
    records = _write_obligation_records(run)
    denominator = len(records)
    if denominator == 0:
        return MetricResult(
            name="vuf",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["no effective MEM_WRITE events with verification obligations"],
        )
    satisfied = 0
    for record in records:
        obligation = run.obligations.get((record.entry_id, record.update_id))
        if obligation is None:
            continue
        if obligation.status is ObligationStatus.SATISFIED:
            satisfied += 1
    return MetricResult(
        name="vuf",
        status=metric_status,
        value=Rational(satisfied, denominator).to_json(),
    )


def _puf_metric(run: _AuditRunState, metric_status: ResultStatus) -> MetricResult:
    if metric_status is ResultStatus.INVALID:
        return MetricResult(
            name="puf",
            status=ResultStatus.INVALID,
            reasons=["audit status is INVALID"],
        )
    if run.config.verify_deadline_ms is None:
        return MetricResult(
            name="puf",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["verify_deadline_ms is not configured"],
        )
    records = _write_obligation_records(run)
    denominator = len(records)
    if denominator == 0:
        return MetricResult(
            name="puf",
            status=ResultStatus.NOT_COMPUTABLE,
            reasons=["no effective MEM_WRITE events with verification obligations"],
        )
    proven = 0
    for record in records:
        obligation = run.obligations.get((record.entry_id, record.update_id))
        if (
            obligation is not None
            and obligation.status is ObligationStatus.SATISFIED
            and obligation.has_provenance
        ):
            proven += 1
    return MetricResult(
        name="puf",
        status=metric_status,
        value=Rational(proven, denominator).to_json(),
    )


def _write_obligation_records(run: _AuditRunState) -> list[_ObligatedUpdateRecord]:
    return [
        record
        for record in run.obligated_updates
        if record.mutation_type is EventType.MEM_WRITE
    ]


def _exposure_metrics(run: _AuditRunState, metric_status: ResultStatus) -> list[MetricResult]:
    exposure_status = metric_status
    reasons: list[str] = []
    if _has_missing_exposure_state(run):
        run.counters["exposure_missing_state"] = 1
        reasons.append("some Active entries lack weight, ttl_ms, risk_level, or last_touch_ms")
        if run.config.profile is ConformanceProfile.P0:
            exposure_status = ResultStatus.INVALID
        elif run.config.profile is ConformanceProfile.P2:
            exposure_status = ResultStatus.BEST_EFFORT
        else:
            exposure_status = ResultStatus.DEGRADED
    stale_now, risk_now, _zombie_now, _supersedence_now = _compute_report_instant_exposures(
        run,
        run.last_boundary_ms or 0,
    )
    return [
        MetricResult(
            "instantaneous_staleness_exposure",
            exposure_status,
            stale_now.to_json(),
            reasons=reasons,
        ),
        MetricResult(
            "staleness_exposure_integral",
            exposure_status,
            run.stale_exp_int.to_json(),
            "weight_ms",
            reasons,
        ),
        MetricResult(
            "instantaneous_risk_exposure",
            exposure_status,
            risk_now.to_json(),
            reasons=reasons,
        ),
        MetricResult(
            "risk_exposure_integral",
            exposure_status,
            run.risk_exp_int.to_json(),
            "risk_weight_ms",
            reasons,
        ),
        MetricResult("zombie_count", metric_status, run.counters["zombie_references"]),
        MetricResult(
            "zombie_delay",
            metric_status,
            sorted(run.zombie_delays_ms),
            "ms",
        ),
        MetricResult(
            "zombie_exposure_integral",
            exposure_status,
            run.zombie_exp_int.to_json(),
            "weight_ms",
            reasons,
        ),
        MetricResult("supersedence_count", metric_status, run.counters["superseded_references"]),
        MetricResult(
            "supersedence_delay",
            metric_status,
            sorted(run.supersedence_delays_ms),
            "ms",
        ),
        MetricResult(
            "supersedence_exposure_integral",
            exposure_status,
            run.supersedence_exp_int.to_json(),
            "weight_ms",
            reasons,
        ),
    ]


def _has_missing_exposure_state(run: _AuditRunState) -> bool:
    for entry in run.entries.values():
        if entry.state is EntryLifecycle.ACTIVE:
            if (
                entry.weight is None
                or entry.ttl_ms is None
                or entry.risk_level is None
                or entry.last_touch_ms is None
            ):
                return True
        elif entry.state in {EntryLifecycle.TOMBSTONED, EntryLifecycle.SUPERSEDED}:
            if entry.weight is None:
                return True
    return False


def _certificate(
    *,
    validation: ValidationReport,
    run: _AuditRunState,
    input_sha256: str | None,
    forced_status: ResultStatus | None = None,
) -> AuditCertificate:
    diagnostics = [*validation.diagnostics, *run.diagnostics]
    status = _certificate_status(
        diagnostics,
        profile=run.config.profile,
        forced_status=forced_status,
    )

    metrics = _build_metrics(run, status)

    return AuditCertificate(
        tool_version=__version__,
        profile=run.config.profile.value,
        status=status,
        input_sha256=input_sha256,
        config=_config_to_dict(run.config),
        validation=validation.to_dict(include_events=True),
        counters=run.counters,
        metrics=metrics,
        entries=sorted(run.entries.values(), key=lambda item: item.entry_id),
        obligations=sorted(
            run.obligations.values(),
            key=lambda item: (item.entry_id, item.update_id),
        ),
        diagnostics=[item.to_dict() for item in diagnostics],
        theory_semantics=THEORY_SEMANTICS,
    )


def _config_to_dict(config: AuditConfig) -> dict[str, Any]:
    risk_weight_map: dict[str, int] | None = None
    if config.risk_weight_map is not None:
        risk_weight_map = {
            str(key): config.risk_weight_map[key] for key in sorted(config.risk_weight_map)
        }
    return {
        "profile": config.profile.value,
        "uptake_horizon_ms": config.uptake_horizon_ms,
        "correction_horizon_ms": config.correction_horizon_ms,
        "verify_deadline_ms": config.verify_deadline_ms,
        "risk_weight_map": risk_weight_map,
        "risk_weight_default": "identity" if config.risk_weight_map is None else "declared_map",
    }


def _certificate_status(
    diagnostics: list[Diagnostic],
    *,
    profile: ConformanceProfile,
    forced_status: ResultStatus | None,
) -> ResultStatus:
    if forced_status is not None:
        return forced_status
    invalid = False
    best_effort = False
    degraded = False
    for diagnostic in diagnostics:
        explicit_status = _diagnostic_status(diagnostic)
        if explicit_status is ResultStatus.INVALID:
            invalid = True
            continue
        if explicit_status is ResultStatus.BEST_EFFORT:
            best_effort = True
            continue
        if explicit_status is ResultStatus.DEGRADED:
            degraded = True
            continue

        if diagnostic.severity is DiagnosticSeverity.ERROR:
            if (
                profile is not ConformanceProfile.P0
                and diagnostic.code in DOWNGRADABLE_VALIDATION_ERROR_CODES
            ):
                degraded = True
            else:
                invalid = True
            continue
        if diagnostic.severity is DiagnosticSeverity.WARNING:
            if profile is ConformanceProfile.P0 and diagnostic.code in P0_FAIL_CLOSED_WARNING_CODES:
                invalid = True
            else:
                degraded = True

    if invalid:
        return ResultStatus.INVALID
    if best_effort:
        return ResultStatus.BEST_EFFORT
    if degraded:
        return ResultStatus.DEGRADED
    return ResultStatus.VALID


def _diagnostic_status(diagnostic: Diagnostic) -> ResultStatus | None:
    if diagnostic.context is None:
        return None
    value = diagnostic.context.get("status")
    if not isinstance(value, str):
        return None
    try:
        return ResultStatus(value)
    except ValueError:
        return None


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()
