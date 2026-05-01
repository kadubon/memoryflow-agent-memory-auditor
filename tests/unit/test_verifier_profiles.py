import json
from pathlib import Path

from memoryflow.profiles import ConformanceProfile
from memoryflow.status import ResultStatus
from memoryflow.verifier import AuditConfig, audit_jsonl_file


def _base(event_type: str, seq: int) -> dict[str, object]:
    return {
        "schema": "memoryflow/1.0",
        "event_type": event_type,
        "collector_id": "collector-a",
        "collector_seq": seq,
        "event_id": f"evt-{seq:04d}",
        "obs_time": f"2026-01-03T01:00:{seq:02d}.000Z",
        "skew_budget_ms": 200,
    }


def _write_event(seq: int = 1) -> dict[str, object]:
    return _base("MEM_WRITE", seq) | {
        "entry_id": "entry-001",
        "content_digest": "sha256:aaa",
        "update_id": "update-001",
        "weight": {"num": "1", "den": "1"},
        "ttl_ms": 1000,
        "risk_level": 1,
    }


def _write_event_2(seq: int = 1) -> dict[str, object]:
    return _base("MEM_WRITE", seq) | {
        "entry_id": "entry-002",
        "content_digest": "sha256:bbb",
        "update_id": "update-002",
        "weight": {"num": "1", "den": "1"},
        "ttl_ms": 1000,
        "risk_level": 1,
    }


def _write_jsonl(tmp_path: Path, events: list[dict[str, object]]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    return path


def test_p0_fails_closed_on_write_without_declare(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path, [_write_event()])

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "P0_MISSING_DECLARE" for item in data["diagnostics"])
    assert data["counters"]["effective_writes"] == 0
    assert data["entries"][0]["state"] == "Unknown"


def test_p1_downgrades_and_processes_implicit_write(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path, [_write_event()])

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()

    assert cert.status is ResultStatus.DEGRADED
    assert any(item["code"] == "IMPLICIT_CREATE_ON_WRITE" for item in data["diagnostics"])
    assert data["counters"]["implicit_creates"] == 1
    assert data["counters"]["effective_writes"] == 1
    assert data["entries"][0]["state"] == "Active"


def test_p0_unsorted_input_lines_remain_valid_after_deterministic_sort(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
    ]
    path = _write_jsonl(tmp_path, list(reversed(events)))

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["effective_writes"] == 1
    assert any(item["code"] == "INPUT_NOT_IN_DETERMINISTIC_ORDER" for item in data["diagnostics"])


def test_p0_duplicate_event_id_noop_does_not_degrade_certificate(
    tmp_path: Path,
) -> None:
    duplicate = _write_event(3) | {"event_id": "evt-0002"}
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        duplicate,
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.VALID
    assert data["validation"]["duplicate_count"] == 1
    assert data["counters"]["effective_writes"] == 1
    assert any(item["code"] == "DUPLICATE_EVENT_ID" for item in data["diagnostics"])


def test_p1_does_not_apply_impossible_delete_transition(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path, [_base("MEM_DELETE", 1) | {"entry_id": "entry-001"}])

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["effective_deletes"] == 0
    assert data["entries"][0]["state"] == "Unknown"
    assert any(item["code"] == "DELETE_UNKNOWN_ENTRY" for item in data["diagnostics"])


def test_p2_allows_unknown_version_read_without_validating_version_metrics(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MEM_READ", 2) | {"entry_id": "entry-001", "request_id": "request-001"},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P2))
    data = cert.to_dict()

    assert cert.status is ResultStatus.BEST_EFFORT
    assert data["counters"]["unknown_version_reads"] == 1
    assert any(item["code"] == "P2_UNKNOWN_VERSION_READ" for item in data["diagnostics"])


def test_p2_unknown_version_read_makes_read_uptake_noncomparable(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_READ", 3) | {"entry_id": "entry-001", "request_id": "request-001"},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P2, uptake_horizon_ms=5000),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert cert.status is ResultStatus.BEST_EFFORT
    assert metrics["read_uptake"]["status"] == "NONCOMPARABLE"


def test_p0_fails_closed_when_event_id_is_missing(tmp_path: Path) -> None:
    event = _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"}
    del event["event_id"]
    path = _write_jsonl(tmp_path, [event])

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "P0_MISSING_EVENT_ID_FAIL_CLOSED" for item in data["diagnostics"])
    assert {item["status"] for item in data["metrics"]} == {"INVALID"}


def test_p1_downgrades_collector_sequence_violation_without_losing_determinism(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 1)
        | {
            "entry_id": "entry-002",
            "event_id": "evt-later-low-seq",
            "obs_time": "2026-01-03T01:00:03.000Z",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["events_processed"] == 2
    assert any(
        item["code"] == "COLLECTOR_SEQ_NOT_STRICTLY_INCREASING_DOWNGRADED"
        for item in data["diagnostics"]
    )


def test_p0_skew_violation_is_invalid(tmp_path: Path) -> None:
    event = _base("MOS_DECLARE", 1) | {
        "entry_id": "entry-001",
        "event_time": "2026-01-03T01:00:02.000Z",
    }
    path = _write_jsonl(tmp_path, [event])

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "SKEW_BUDGET_EXCEEDED" for item in cert.to_dict()["diagnostics"])


def test_p0_fails_closed_on_noncanonical_digest_declaration(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2) | {"canon_alg": "none"},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))

    assert cert.status is ResultStatus.INVALID
    assert any(
        item["code"] == "NON_CANONICAL_DIGEST_DECLARED"
        for item in cert.to_dict()["diagnostics"]
    )


def test_zombie_reference_is_quality_observation_not_audit_invalidity(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_DELETE", 3) | {"entry_id": "entry-001"},
        _base("MEM_READ", 4)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["zombie_references"] == 1
    assert any(item["code"] == "ZOMBIE_REFERENCE_OBSERVED" for item in data["diagnostics"])
    metrics = {item["name"]: item for item in data["metrics"]}
    assert metrics["zombie_delay"]["value"] == [1000]


def test_terminal_exposure_integral_is_invalid_when_pre_terminal_weight_is_missing(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MEM_DELETE", 2) | {"entry_id": "entry-001"},
        _base("MEM_READ", 3)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.INVALID
    assert data["counters"]["exposure_missing_state"] == 1
    assert metrics["zombie_count"]["value"] == 1
    assert metrics["zombie_exposure_integral"]["status"] == "INVALID"


def test_capped_read_selection_controls_read_uptake(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _write_event_2(4),
        _base("MEM_READ", 5)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
            "rank": 1,
            "cap": 1,
        },
        _base("MEM_READ", 6)
        | {
            "entry_id": "entry-002",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
            "request_id": "request-001",
            "rank": 0,
            "cap": 1,
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, uptake_horizon_ms=5000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["read_truncated_by_cap"] == 1
    assert metrics["read_uptake"]["value"] == {"num": "1", "den": "2"}
    assert any(item["code"] == "READ_TRUNCATED_BY_CAP" for item in data["diagnostics"])


def test_capped_read_selection_excludes_unselected_zombie_read(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _write_event_2(4),
        _base("MEM_DELETE", 5) | {"entry_id": "entry-001"},
        _base("MEM_READ", 6)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
            "rank": 1,
            "cap": 1,
        },
        _base("MEM_READ", 7)
        | {
            "entry_id": "entry-002",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
            "request_id": "request-001",
            "rank": 0,
            "cap": 1,
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["zombie_references"] == 0
    assert data["counters"]["read_truncated_by_cap"] == 1


def test_p0_fails_closed_when_capped_read_group_lacks_rank(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _write_event_2(4),
        _base("MEM_READ", 5)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
            "cap": 1,
        },
        _base("MEM_READ", 6)
        | {
            "entry_id": "entry-002",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
            "request_id": "request-001",
            "rank": 0,
            "cap": 1,
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert data["counters"]["read_selection_invalid"] == 1
    assert any(item["code"] == "READ_CAP_MISSING_RANK" for item in data["diagnostics"])


def test_supersedence_reference_records_delay_and_integral(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _base("MEM_REPLACE", 4)
        | {
            "old_entry_id": "entry-001",
            "new_entry_id": "entry-002",
            "old_update_id": "update-001",
        },
        _base("MEM_USE", 6)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["superseded_references"] == 1
    assert metrics["supersedence_delay"]["value"] == [2000]
    assert metrics["supersedence_exposure_integral"]["value"] == {"num": "2000", "den": "1"}


def test_p0_replace_requires_declared_new_entry(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_REPLACE", 3)
        | {
            "old_entry_id": "entry-001",
            "new_entry_id": "entry-002",
            "old_update_id": "update-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    states = {item["entry_id"]: item["state"] for item in data["entries"]}

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "REPLACE_TARGET_UNKNOWN_ENTRY" for item in data["diagnostics"])
    assert states["entry-001"] == "Active"


def test_p1_does_not_apply_replace_from_non_active_entry(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-002"},
        _write_event_2(2),
        _base("MEM_REPLACE", 3)
        | {
            "old_entry_id": "entry-001",
            "new_entry_id": "entry-002",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["effective_replaces"] == 0
    assert any(item["code"] == "REPLACE_NON_ACTIVE_ENTRY" for item in data["diagnostics"])


def test_p0_replace_checks_declared_new_update_id(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _write_event_2(4),
        _base("MEM_REPLACE", 5)
        | {
            "old_entry_id": "entry-001",
            "new_entry_id": "entry-002",
            "old_update_id": "update-001",
            "new_update_id": "wrong-update",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "REPLACE_NEW_UPDATE_MISMATCH" for item in data["diagnostics"])


def test_p0_replace_rejects_terminal_replacement_target(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _base("MOS_DECLARE", 2) | {"entry_id": "entry-002"},
        _write_event(3),
        _write_event_2(4),
        _base("MEM_DELETE", 5) | {"entry_id": "entry-002"},
        _base("MEM_REPLACE", 6)
        | {
            "old_entry_id": "entry-001",
            "new_entry_id": "entry-002",
            "old_update_id": "update-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    states = {item["entry_id"]: item["state"] for item in data["entries"]}

    assert cert.status is ResultStatus.INVALID
    assert data["counters"]["effective_replaces"] == 0
    assert states["entry-001"] == "Active"
    assert states["entry-002"] == "Tombstoned"
    assert any(item["code"] == "REPLACE_TARGET_NOT_ACTIVE" for item in data["diagnostics"])


def test_verification_obligation_is_satisfied_by_matching_pass(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2)
        | {
            "provenance": {
                "kind": "hash_link",
                "hash_alg": "sha256",
                "source_uri": "urn:test:source",
                "source_digest": "sha256:source",
            }
        },
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=2000),
    )
    data = cert.to_dict()

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["verification_obligations"] == 1
    assert data["counters"]["verification_passes_satisfied"] == 1
    assert data["obligations"][0]["status"] == "SATISFIED"
    assert data["obligations"][0]["deadline_ms"] == 1767402004000
    metrics = {item["name"]: item for item in data["metrics"]}
    assert metrics["vuf"]["value"] == {"num": "1", "den": "1"}
    assert metrics["puf"]["value"] == {"num": "1", "den": "1"}


def test_verification_after_delete_still_satisfies_version_bound_vuf(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_DELETE", 3) | {"entry_id": "entry-001"},
        _base("MEM_VERIFY", 4)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=5000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["obligations"][0]["status"] == "SATISFIED"
    assert metrics["vuf"]["value"] == {"num": "1", "den": "1"}


def test_p0_verify_without_obligation_is_invalid(tmp_path: Path) -> None:
    events = [
        _base("MEM_VERIFY", 1)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "VERIFY_WITHOUT_OBLIGATION" for item in data["diagnostics"])


def test_unsatisfied_verification_obligation_expires_after_deadline(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_READ", 5)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=2000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["verification_obligations_expired"] == 1
    assert data["obligations"][0]["status"] == "EXPIRED"
    assert data["obligations"][0]["expired_ms"] == 1767402004000
    assert metrics["vuf"]["value"] == {"num": "0", "den": "1"}
    assert metrics["puf"]["value"] == {"num": "0", "den": "1"}


def test_fail_after_obligation_expiry_still_records_correction_latency(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 5)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _base("MEM_CORRECT", 6)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(
            profile=ConformanceProfile.P0,
            correction_horizon_ms=2000,
            verify_deadline_ms=1000,
        ),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["obligations"][0]["status"] == "EXPIRED"
    assert data["counters"]["verification_failures"] == 1
    assert metrics["correction_latency"]["value"] == [1000]


def test_staleness_and_risk_exposure_integrals_are_exact(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_READ", 4)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert metrics["instantaneous_staleness_exposure"]["value"] == {"num": "1", "den": "1"}
    assert metrics["staleness_exposure_integral"]["value"] == {"num": "1000", "den": "1"}
    assert metrics["risk_exposure_integral"]["value"] == {"num": "1000", "den": "1"}


def test_no_op_write_does_not_refresh_staleness_boundary(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _write_event(3) | {"event_id": "evt-noop"},
        _base("MEM_READ", 4)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["effective_writes"] == 1
    assert any(item["code"] == "NO_OP_WRITE_IGNORED" for item in data["diagnostics"])
    assert metrics["staleness_exposure_integral"]["value"] == {"num": "1000", "den": "1"}


def test_p0_rejects_update_id_digest_rebinding(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _write_event(3) | {"event_id": "evt-rebind", "content_digest": "sha256:bbb"},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert data["counters"]["effective_writes"] == 1
    assert any(item["code"] == "UPDATE_ID_DIGEST_REBIND" for item in data["diagnostics"])


def test_p0_rejects_update_id_reuse_for_non_current_version(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_CORRECT", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
        _write_event(4) | {"event_id": "evt-reuse"},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert data["counters"]["effective_writes"] == 1
    assert any(item["code"] == "UPDATE_ID_REUSE" for item in data["diagnostics"])


def test_declared_risk_weight_map_controls_risk_exposure(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2) | {"risk_level": 3},
        _base("MEM_READ", 4)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(
            profile=ConformanceProfile.P0,
            risk_weight_map={3: 10},
        ),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["staleness_exposure_integral"]["value"] == {"num": "1000", "den": "1"}
    assert metrics["risk_exposure_integral"]["value"] == {"num": "10000", "den": "1"}


def test_p0_fails_when_declared_risk_map_omits_observed_risk_level(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2) | {"risk_level": 7},
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, risk_weight_map={1: 1}),
    )
    data = cert.to_dict()

    assert cert.status is ResultStatus.INVALID
    assert any(item["code"] == "RISK_WEIGHT_UNDECLARED" for item in data["diagnostics"])


def test_instantaneous_staleness_is_not_counted_at_exact_ttl_boundary(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_READ", 3)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["instantaneous_staleness_exposure"]["value"] == {"num": "0", "den": "1"}
    assert metrics["staleness_exposure_integral"]["value"] == {"num": "0", "den": "1"}


def test_invalid_provenance_does_not_count_for_puf(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2) | {"provenance": {"kind": "unknown"}},
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P1, verify_deadline_ms=2000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.DEGRADED
    assert metrics["vuf"]["value"] == {"num": "1", "den": "1"}
    assert metrics["puf"]["value"] == {"num": "0", "den": "1"}
    assert any(item["code"] == "INVALID_PROVENANCE" for item in data["diagnostics"])


def test_verify_provenance_does_not_satisfy_write_bound_puf(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
            "provenance": {
                "kind": "hash_link",
                "hash_alg": "sha256",
                "source_uri": "urn:test:source",
                "source_digest": "sha256:source",
            },
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=2000),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert metrics["puf"]["value"] == {"num": "0", "den": "1"}


def test_uptake_uses_mem_use_within_declared_horizon(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_USE", 3)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, uptake_horizon_ms=1500),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["uptake"]["value"] == {"num": "1", "den": "1"}
    assert metrics["read_uptake"]["value"] == {"num": "0", "den": "1"}


def test_p1_reference_mismatch_does_not_count_for_uptake(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_USE", 3)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:aaa",
            "update_id": "wrong-update",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P1, uptake_horizon_ms=5000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.DEGRADED
    assert metrics["uptake"]["value"] == {"num": "0", "den": "1"}
    assert any(item["code"] == "REFERENCE_UPDATE_MISMATCH" for item in data["diagnostics"])


def test_correction_latency_and_fraction_uncorrected(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _base("MEM_CORRECT", 4)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
        _base("MEM_READ", 6)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
            "request_id": "request-002",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, correction_horizon_ms=2000),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["correction_latency"]["value"] == [1000]
    assert metrics["fraction_uncorrected_within_horizon"]["value"] == {
        "num": "0",
        "den": "1",
    }


def test_correction_latency_records_matching_target_after_intervening_mutation(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _write_event(4)
        | {
            "event_id": "evt-update-002",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
        },
        _base("MEM_CORRECT", 5)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:ccc",
            "corrected_update_id": "update-003",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["corrections_matched_to_failure"] == 1
    assert data["counters"]["effective_corrections"] == 0
    assert metrics["correction_latency"]["value"] == [2000]
    assert any(item["code"] == "CORRECT_UPDATE_MISMATCH" for item in data["diagnostics"])


def test_correction_latency_records_matching_target_after_terminal_transition(
    tmp_path: Path,
) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _base("MEM_DELETE", 4) | {"entry_id": "entry-001"},
        _base("MEM_CORRECT", 5)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["corrections_matched_to_failure"] == 1
    assert data["counters"]["effective_corrections"] == 0
    assert metrics["correction_latency"]["value"] == [2000]
    assert any(item["code"] == "CORRECT_TERMINAL_ENTRY" for item in data["diagnostics"])


def test_no_op_correction_does_not_satisfy_correction_latency(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _base("MEM_CORRECT", 4)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:aaa",
            "corrected_update_id": "update-001",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert data["counters"]["corrections_matched_to_failure"] == 0
    assert metrics["correction_latency"]["value"] == []
    assert any(item["code"] == "NO_OP_CORRECTION_IGNORED" for item in data["diagnostics"])


def test_effective_correction_is_not_in_vuf_and_puf_denominator(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
        _base("MEM_CORRECT", 4)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
            "provenance": {
                "kind": "hash_link",
                "hash_alg": "sha256",
                "source_uri": "urn:test:correction",
                "source_digest": "sha256:source",
            },
        },
        _base("MEM_VERIFY", 5)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:bbb",
            "target_update_id": "update-002",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=5000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert data["counters"]["verification_obligations"] == 2
    assert data["counters"]["write_verification_obligations"] == 1
    assert data["counters"]["correction_verification_obligations"] == 1
    assert metrics["vuf"]["value"] == {"num": "0", "den": "1"}
    assert metrics["puf"]["value"] == {"num": "0", "den": "1"}


def test_corrected_version_use_does_not_inflate_write_uptake(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_CORRECT", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
        _base("MEM_USE", 4)
        | {
            "entry_id": "entry-001",
            "content_digest": "sha256:bbb",
            "update_id": "update-002",
            "request_id": "request-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, uptake_horizon_ms=5000),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["uptake"]["value"] == {"num": "0", "den": "1"}


def test_p1_does_not_apply_correction_with_target_mismatch(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_CORRECT", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:wrong",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()
    entries = {item["entry_id"]: item for item in data["entries"]}

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["effective_corrections"] == 0
    assert entries["entry-001"]["current_update_id"] == "update-001"
    assert any(item["code"] == "CORRECT_DIGEST_MISMATCH" for item in data["diagnostics"])


def test_p1_does_not_apply_correction_to_unknown_entry(tmp_path: Path) -> None:
    events = [
        _base("MEM_CORRECT", 1)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P1))
    data = cert.to_dict()

    assert cert.status is ResultStatus.DEGRADED
    assert data["counters"]["effective_corrections"] == 0
    assert data["entries"][0]["state"] == "Unknown"
    assert any(item["code"] == "CORRECT_UNKNOWN_ENTRY" for item in data["diagnostics"])


def test_fraction_uncorrected_excludes_unmatured_failures(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_VERIFY", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "FAIL",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, correction_horizon_ms=2000),
    )
    metrics = {item["name"]: item for item in cert.to_dict()["metrics"]}

    assert metrics["fraction_uncorrected_within_horizon"]["status"] == "NOT_COMPUTABLE"


def test_verification_after_next_mutation_does_not_satisfy_vuf(tmp_path: Path) -> None:
    events = [
        _base("MOS_DECLARE", 1) | {"entry_id": "entry-001"},
        _write_event(2),
        _base("MEM_CORRECT", 3)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "corrected_digest": "sha256:bbb",
            "corrected_update_id": "update-002",
            "correction_class": "factual",
        },
        _base("MEM_VERIFY", 4)
        | {
            "target_entry_id": "entry-001",
            "target_digest": "sha256:aaa",
            "target_update_id": "update-001",
            "verdict": "PASS",
            "verifier_id": "verifier-001",
        },
    ]
    path = _write_jsonl(tmp_path, events)

    cert = audit_jsonl_file(
        path,
        config=AuditConfig(profile=ConformanceProfile.P0, verify_deadline_ms=5000),
    )
    data = cert.to_dict()
    metrics = {item["name"]: item for item in data["metrics"]}

    assert cert.status is ResultStatus.VALID
    assert metrics["vuf"]["value"] == {"num": "0", "den": "1"}
    assert any(item["code"] == "VERIFY_AFTER_OBLIGATION_CLOSED" for item in data["diagnostics"])
