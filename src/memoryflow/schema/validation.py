"""Strict MemoryFlow JSONL validation."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from memoryflow.constants import (
    MAX_DIGEST_BYTES,
    MAX_EVENT_LINE_BYTES,
    MAX_IDENTIFIER_BYTES,
    MAX_JSON_NESTING_DEPTH,
    SCHEMA_VERSION,
    TTL_INF_MS,
)
from memoryflow.diagnostics import Diagnostic, DiagnosticSeverity, has_errors
from memoryflow.events import EventEnvelope, EventType, MemoryFlowEvent, TimestampError
from memoryflow.events.time import parse_rfc3339_to_ms
from memoryflow.ordering import (
    check_collector_seq_monotonicity,
    check_input_order,
    check_order_key_collisions,
    deduplicate_events,
    sort_events,
)
from memoryflow.rational import Rational, RationalError

COMMON_REQUIRED = {
    "schema",
    "event_type",
    "collector_id",
    "collector_seq",
    "obs_time",
    "skew_budget_ms",
}
COMMON_OPTIONAL = {
    "event_id",
    "event_time",
    "extensions",
    "canon_alg",
    "hash_alg",
}

EVENT_REQUIRED: dict[EventType, set[str]] = {
    EventType.MOS_DECLARE: {"entry_id"},
    EventType.MEM_WRITE: {
        "entry_id",
        "content_digest",
        "update_id",
        "weight",
        "ttl_ms",
        "risk_level",
    },
    EventType.MEM_REPLACE: {"old_entry_id", "new_entry_id"},
    EventType.MEM_DELETE: {"entry_id"},
    EventType.MEM_READ: {"entry_id", "content_digest", "update_id", "request_id"},
    EventType.MEM_USE: {"entry_id", "content_digest", "update_id", "request_id"},
    EventType.MEM_VERIFY: {
        "target_entry_id",
        "target_digest",
        "target_update_id",
        "verdict",
        "verifier_id",
    },
    EventType.MEM_CORRECT: {
        "target_entry_id",
        "target_digest",
        "target_update_id",
        "corrected_digest",
        "corrected_update_id",
        "correction_class",
    },
}

EVENT_OPTIONAL: dict[EventType, set[str]] = {
    EventType.MOS_DECLARE: {
        "weight",
        "ttl_ms",
        "risk_level",
        "content_digest",
        "update_id",
    },
    EventType.MEM_WRITE: {"provenance"},
    EventType.MEM_REPLACE: {"old_update_id", "new_update_id"},
    EventType.MEM_DELETE: {"delete_reason"},
    EventType.MEM_READ: {"rank", "cap"},
    EventType.MEM_USE: set(),
    EventType.MEM_VERIFY: {"provenance"},
    EventType.MEM_CORRECT: {
        "corrected_weight",
        "corrected_ttl_ms",
        "corrected_risk_level",
        "provenance",
    },
}

IDENTIFIER_FIELDS = {
    "collector_id",
    "event_id",
    "entry_id",
    "old_entry_id",
    "new_entry_id",
    "update_id",
    "old_update_id",
    "new_update_id",
    "request_id",
    "target_entry_id",
    "target_update_id",
    "corrected_update_id",
    "verifier_id",
    "delete_reason",
    "correction_class",
}
DIGEST_FIELDS = {"content_digest", "target_digest", "corrected_digest"}
NONNEGATIVE_INT_FIELDS = {
    "collector_seq",
    "skew_budget_ms",
    "ttl_ms",
    "risk_level",
    "rank",
    "cap",
    "corrected_ttl_ms",
    "corrected_risk_level",
}
RATIONAL_FIELDS = {"weight", "corrected_weight"}


@dataclass(frozen=True)
class ValidationReport:
    total_lines: int
    event_count: int
    kept_event_count: int
    duplicate_count: int
    missing_event_id_count: int
    diagnostics: list[Diagnostic]
    events: list[MemoryFlowEvent]

    @property
    def is_valid(self) -> bool:
        return not has_errors(self.diagnostics)

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "valid": self.is_valid,
            "total_lines": self.total_lines,
            "event_count": self.event_count,
            "kept_event_count": self.kept_event_count,
            "duplicate_count": self.duplicate_count,
            "missing_event_id_count": self.missing_event_id_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
        if include_events:
            data["events"] = [
                {
                    "line": event.line,
                    "event_type": event.event_type.value,
                    "event_id": event.event_id,
                    "order_key": list(event.order_key),
                }
                for event in self.events
            ]
        return data


def validate_jsonl_file(
    path: Path | str,
    *,
    max_line_bytes: int = MAX_EVENT_LINE_BYTES,
    allow_p2_read_without_version: bool = False,
) -> ValidationReport:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as handle:
            return validate_jsonl_lines(
                handle,
                max_line_bytes=max_line_bytes,
                allow_p2_read_without_version=allow_p2_read_without_version,
            )
    except OSError as exc:
        return ValidationReport(
            total_lines=0,
            event_count=0,
            kept_event_count=0,
            duplicate_count=0,
            missing_event_id_count=0,
            diagnostics=[
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="JSONL_READ_ERROR",
                    message=f"could not read JSONL file: {_safe_os_error_message(exc)}",
                    context={"errno": exc.errno},
                )
            ],
            events=[],
        )


def validate_jsonl_lines(
    lines: Iterable[str],
    *,
    max_line_bytes: int = MAX_EVENT_LINE_BYTES,
    allow_p2_read_without_version: bool = False,
) -> ValidationReport:
    diagnostics: list[Diagnostic] = []
    events: list[MemoryFlowEvent] = []
    total_lines = 0
    missing_event_id_count = 0

    for line_no, line in enumerate(lines, start=1):
        total_lines = line_no
        if len(line.encode("utf-8")) > max_line_bytes:
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="JSONL_LINE_TOO_LARGE",
                    message=f"JSONL line exceeds {max_line_bytes} bytes",
                    line=line_no,
                )
            )
            continue
        if not line.strip():
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="JSONL_BLANK_LINE",
                    message="blank lines are not valid strict JSONL events",
                    line=line_no,
                )
            )
            continue
        record, parse_diagnostics = _parse_json_line(line, line_no)
        diagnostics.extend(parse_diagnostics)
        if record is None:
            continue
        event, event_diagnostics = validate_record(
            record,
            line=line_no,
            allow_p2_read_without_version=allow_p2_read_without_version,
        )
        diagnostics.extend(event_diagnostics)
        if event is not None:
            events.append(event)
            if event.event_id is None:
                missing_event_id_count += 1

    diagnostics.extend(check_input_order(events))
    kept_events, dedup_diagnostics, duplicate_count = deduplicate_events(events)
    diagnostics.extend(dedup_diagnostics)
    diagnostics.extend(check_order_key_collisions(kept_events))
    diagnostics.extend(check_collector_seq_monotonicity(kept_events))

    return ValidationReport(
        total_lines=total_lines,
        event_count=len(events),
        kept_event_count=len(kept_events),
        duplicate_count=duplicate_count,
        missing_event_id_count=missing_event_id_count,
        diagnostics=diagnostics,
        events=sort_events(kept_events),
    )


def validate_record(
    record: dict[str, Any],
    *,
    line: int | None = None,
    allow_p2_read_without_version: bool = False,
) -> tuple[MemoryFlowEvent | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    if not isinstance(record, dict):
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="EVENT_NOT_OBJECT",
                message="each JSONL line must decode to a JSON object",
                line=line,
            )
        ]

    event_type = _parse_event_type(record.get("event_type"), line, diagnostics)
    allowed_keys = set(COMMON_REQUIRED) | set(COMMON_OPTIONAL)
    if event_type is not None:
        allowed_keys |= EVENT_REQUIRED[event_type] | EVENT_OPTIONAL[event_type]
    for key in sorted(set(record) - allowed_keys):
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="UNKNOWN_FIELD",
                message="unknown event field is not allowed outside extensions",
                line=line,
                field=key,
            )
        )

    for key in sorted(COMMON_REQUIRED):
        if key not in record:
            diagnostics.append(_missing_required(key, line))
    if event_type is not None:
        required_fields = _profile_adjusted_required_fields(
            event_type,
            allow_p2_read_without_version=allow_p2_read_without_version,
        )
        for key in sorted(required_fields):
            if key not in record:
                diagnostics.append(_missing_required(key, line))
        if event_type is EventType.MEM_READ and allow_p2_read_without_version:
            for key in ("content_digest", "update_id"):
                if key not in record:
                    diagnostics.append(
                        Diagnostic(
                            severity=DiagnosticSeverity.WARNING,
                            code="MISSING_READ_VERSION_BINDING",
                            message=(
                                f"{key} is missing on MEM_READ; P2 may treat this as "
                                "unknown-version, but version-sensitive metrics are not valid"
                            ),
                            line=line,
                            field=key,
                        )
                    )

    schema = _require_string(record.get("schema"), "schema", line, diagnostics)
    if schema is not None and schema != SCHEMA_VERSION:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="UNSUPPORTED_SCHEMA",
                message=f"schema must be {SCHEMA_VERSION}",
                line=line,
                field="schema",
            )
        )

    collector_id = _require_identifier(
        record.get("collector_id"), "collector_id", line, diagnostics
    )
    collector_seq = _require_nonnegative_int(
        record.get("collector_seq"), "collector_seq", line, diagnostics
    )
    skew_budget_ms = _require_nonnegative_int(
        record.get("skew_budget_ms"), "skew_budget_ms", line, diagnostics
    )

    event_id = _parse_event_id(record, line, diagnostics)
    obs_time = _require_string(record.get("obs_time"), "obs_time", line, diagnostics)
    obs_time_ms = _parse_timestamp_field(record.get("obs_time"), "obs_time", line, diagnostics)

    event_time = record.get("event_time")
    event_time_ms: int | None = None
    if event_time is not None:
        _require_string(event_time, "event_time", line, diagnostics)
        event_time_ms = _parse_timestamp_field(event_time, "event_time", line, diagnostics)

    _validate_common_optional(record, line, diagnostics)
    if event_type is not None:
        _validate_payload(record, event_type, line, diagnostics)

    if has_errors(diagnostics):
        return None, diagnostics

    if (
        schema is None
        or event_type is None
        or collector_id is None
        or collector_seq is None
        or obs_time is None
        or obs_time_ms is None
        or skew_budget_ms is None
    ):
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INTERNAL_VALIDATION_INCOMPLETE",
                message="event could not be constructed after validation",
                line=line,
            )
        )
        return None, diagnostics

    envelope = EventEnvelope(
        schema=schema,
        event_type=event_type,
        collector_id=collector_id,
        collector_seq=collector_seq,
        event_id=event_id,
        obs_time=obs_time,
        obs_time_ms=obs_time_ms,
        skew_budget_ms=skew_budget_ms,
        event_time=event_time if isinstance(event_time, str) else None,
        event_time_ms=event_time_ms,
    )
    common_fields = COMMON_REQUIRED | COMMON_OPTIONAL
    payload = {key: value for key, value in record.items() if key not in common_fields}
    event = MemoryFlowEvent(envelope=envelope, payload=payload, line=line or 0, raw=record)
    return event, diagnostics


def _parse_json_line(line: str, line_no: int) -> tuple[dict[str, Any] | None, list[Diagnostic]]:
    try:
        value = json.loads(
            line,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="JSON_PARSE_ERROR",
                message=f"invalid JSON: {exc.msg}",
                line=line_no,
                context={"column": exc.colno},
            )
        ]
    except ValueError as exc:
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="JSON_PARSE_ERROR",
                message=f"invalid JSON: {exc}",
                line=line_no,
            )
        ]
    except RecursionError:
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="JSON_NESTING_TOO_DEEP",
                message=f"JSON nesting exceeds {MAX_JSON_NESTING_DEPTH} levels",
                line=line_no,
            )
        ]
    if not isinstance(value, dict):
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="EVENT_NOT_OBJECT",
                message="each JSONL line must decode to a JSON object",
                line=line_no,
            )
        ]
    if not _json_depth_within_limit(value, MAX_JSON_NESTING_DEPTH):
        return None, [
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="JSON_NESTING_TOO_DEEP",
                message=f"JSON nesting exceeds {MAX_JSON_NESTING_DEPTH} levels",
                line=line_no,
            )
        ]
    return value, []


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key is not allowed: {key}")
        value[key] = child
    return value


def _safe_os_error_message(exc: OSError) -> str:
    return exc.strerror or exc.__class__.__name__


def _json_depth_within_limit(value: Any, max_depth: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            return False
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    return True


def _missing_required(field: str, line: int | None) -> Diagnostic:
    return Diagnostic(
        severity=DiagnosticSeverity.ERROR,
        code="MISSING_REQUIRED_FIELD",
        message=f"missing required field: {field}",
        line=line,
        field=field,
    )


def _profile_adjusted_required_fields(
    event_type: EventType,
    *,
    allow_p2_read_without_version: bool,
) -> set[str]:
    required = set(EVENT_REQUIRED[event_type])
    if event_type is EventType.MEM_READ and allow_p2_read_without_version:
        required -= {"content_digest", "update_id"}
    return required


def _parse_event_type(
    value: Any, line: int | None, diagnostics: list[Diagnostic]
) -> EventType | None:
    if not isinstance(value, str):
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_EVENT_TYPE",
                message="event_type must be a supported string",
                line=line,
                field="event_type",
            )
        )
        return None
    try:
        return EventType(value)
    except ValueError:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_EVENT_TYPE",
                message="event_type is not a supported MemoryFlow core event",
                line=line,
                field="event_type",
            )
        )
        return None


def _require_string(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> str | None:
    if not isinstance(value, str):
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_STRING",
                message=f"{field} must be a string",
                line=line,
                field=field,
            )
        )
        return None
    return value


def _require_identifier(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> str | None:
    string_value = _require_string(value, field, line, diagnostics)
    if string_value is None:
        return None
    if len(string_value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="IDENTIFIER_TOO_LONG",
                message=f"{field} exceeds {MAX_IDENTIFIER_BYTES} UTF-8 bytes",
                line=line,
                field=field,
            )
        )
        return None
    if string_value == "":
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="EMPTY_IDENTIFIER",
                message=f"{field} must not be empty",
                line=line,
                field=field,
            )
        )
        return None
    return string_value


def _parse_event_id(
    record: dict[str, Any], line: int | None, diagnostics: list[Diagnostic]
) -> str | None:
    if "event_id" not in record:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.WARNING,
                code="MISSING_EVENT_ID",
                message="event_id is missing; deduplication is not guaranteed for this event",
                line=line,
                field="event_id",
            )
        )
        return None
    value = record["event_id"]
    if value is None or value == "":
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.WARNING,
                code="MISSING_EVENT_ID",
                message="event_id is empty; deduplication is not guaranteed for this event",
                line=line,
                field="event_id",
            )
        )
        return None
    return _require_identifier(value, "event_id", line, diagnostics)


def _require_nonnegative_int(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_NONNEGATIVE_INTEGER",
                message=f"{field} must be a nonnegative integer",
                line=line,
                field=field,
            )
        )
        return None
    if value < 0:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_NONNEGATIVE_INTEGER",
                message=f"{field} must be nonnegative",
                line=line,
                field=field,
            )
        )
        return None
    if value > TTL_INF_MS:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INTEGER_TOO_LARGE",
                message=f"{field} exceeds the MemoryFlow integer sentinel",
                line=line,
                field=field,
            )
        )
        return None
    return int(value)


def _parse_timestamp_field(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> int | None:
    try:
        return parse_rfc3339_to_ms(value)
    except TimestampError as exc:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_TIMESTAMP",
                message=str(exc),
                line=line,
                field=field,
            )
        )
        return None


def _validate_common_optional(
    record: dict[str, Any], line: int | None, diagnostics: list[Diagnostic]
) -> None:
    for field in ("extensions",):
        if field in record and not isinstance(record[field], dict):
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="INVALID_OBJECT",
                    message=f"{field} must be an object",
                    line=line,
                    field=field,
                )
            )
    for field in ("canon_alg", "hash_alg"):
        if field in record:
            _require_identifier(record[field], field, line, diagnostics)


def _validate_payload(
    record: dict[str, Any],
    event_type: EventType,
    line: int | None,
    diagnostics: list[Diagnostic],
) -> None:
    for field in IDENTIFIER_FIELDS:
        if field in record:
            _require_identifier(record[field], field, line, diagnostics)
    for field in DIGEST_FIELDS:
        if field in record:
            _require_digest(record[field], field, line, diagnostics)
    for field in NONNEGATIVE_INT_FIELDS:
        if field in record:
            _require_nonnegative_int(record[field], field, line, diagnostics)
    for field in RATIONAL_FIELDS:
        if field in record:
            _require_rational(record[field], field, line, diagnostics)
    if event_type is EventType.MEM_VERIFY and record.get("verdict") not in {"PASS", "FAIL"}:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_VERDICT",
                message="verdict must be PASS or FAIL",
                line=line,
                field="verdict",
            )
        )
    for field in ("provenance",):
        if field in record and not isinstance(record[field], dict):
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="INVALID_OBJECT",
                    message=f"{field} must be an object",
                    line=line,
                    field=field,
                )
            )


def _require_digest(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> str | None:
    string_value = _require_string(value, field, line, diagnostics)
    if string_value is None:
        return None
    if len(string_value.encode("utf-8")) > MAX_DIGEST_BYTES:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="DIGEST_TOO_LONG",
                message=f"{field} exceeds {MAX_DIGEST_BYTES} UTF-8 bytes",
                line=line,
                field=field,
            )
        )
        return None
    if string_value == "":
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="EMPTY_DIGEST",
                message=f"{field} must not be empty",
                line=line,
                field=field,
            )
        )
        return None
    return string_value


def _require_rational(
    value: Any, field: str, line: int | None, diagnostics: list[Diagnostic]
) -> Rational | None:
    try:
        return Rational.from_json(value, field_name=field).require_nonnegative(field_name=field)
    except RationalError as exc:
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="INVALID_RATIONAL",
                message=str(exc),
                line=line,
                field=field,
            )
        )
        return None
