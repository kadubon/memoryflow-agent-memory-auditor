import json
from pathlib import Path

from memoryflow.schema import validate_jsonl_file, validate_jsonl_lines, validate_record
from memoryflow.testing.samples import get_sample_events, get_sample_jsonl


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_sample_jsonl_is_valid(tmp_path: Path) -> None:
    path = _write(tmp_path / "events.jsonl", get_sample_jsonl("stale-memory"))

    report = validate_jsonl_file(path)

    assert report.is_valid
    assert report.total_lines == 3
    assert report.event_count == 3
    assert report.kept_event_count == 3
    assert report.diagnostics == []


def test_streaming_jsonl_lines_validation_matches_file_ingestion() -> None:
    report = validate_jsonl_lines(get_sample_jsonl("stale-memory").splitlines(keepends=True))

    assert report.is_valid
    assert report.total_lines == 3
    assert report.kept_event_count == 3


def test_missing_event_id_is_warning_not_parse_failure(tmp_path: Path) -> None:
    event = get_sample_events("stale-memory")[0]
    del event["event_id"]
    path = _write(tmp_path / "events.jsonl", json.dumps(event) + "\n")

    report = validate_jsonl_file(path)

    assert report.is_valid
    assert report.missing_event_id_count == 1
    assert [item.code for item in report.diagnostics] == ["MISSING_EVENT_ID"]


def test_read_error_diagnostic_does_not_embed_local_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.jsonl"

    report = validate_jsonl_file(missing)

    assert not report.is_valid
    assert report.diagnostics[0].code == "JSONL_READ_ERROR"
    assert str(missing) not in report.diagnostics[0].message


def test_unknown_field_is_error() -> None:
    event = get_sample_events("stale-memory")[0]
    event["unexpected"] = "nope"

    parsed, diagnostics = validate_record(event, line=1)

    assert parsed is None
    assert any(item.code == "UNKNOWN_FIELD" for item in diagnostics)


def test_weight_float_is_invalid(tmp_path: Path) -> None:
    event = get_sample_events("stale-memory")[1]
    event["weight"] = 0.3
    path = _write(tmp_path / "events.jsonl", json.dumps(event) + "\n")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "INVALID_RATIONAL" for item in report.diagnostics)


def test_deep_json_nesting_is_rejected(tmp_path: Path) -> None:
    nested: object = "leaf"
    for _ in range(70):
        nested = {"x": nested}
    path = _write(tmp_path / "events.jsonl", json.dumps(nested) + "\n")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "JSON_NESTING_TOO_DEEP" for item in report.diagnostics)


def test_nonstandard_json_constants_are_rejected(tmp_path: Path) -> None:
    event = get_sample_events("stale-memory")[0]
    text = json.dumps(event)[:-1] + ', "extensions": {"bad": NaN}}'
    path = _write(tmp_path / "events.jsonl", text + "\n")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "JSON_PARSE_ERROR" for item in report.diagnostics)


def test_duplicate_json_object_keys_are_rejected(tmp_path: Path) -> None:
    event = get_sample_events("stale-memory")[0]
    text = json.dumps(event)[:-1] + ', "entry_id": "shadow"}'
    path = _write(tmp_path / "events.jsonl", text + "\n")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "JSON_PARSE_ERROR" for item in report.diagnostics)


def test_enormous_nonnegative_integer_is_rejected(tmp_path: Path) -> None:
    event = get_sample_events("stale-memory")[0]
    event["collector_seq"] = 10**30
    path = _write(tmp_path / "events.jsonl", json.dumps(event) + "\n")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "INTEGER_TOO_LARGE" for item in report.diagnostics)


def test_all_core_event_types_have_minimum_valid_schema() -> None:
    base = {
        "schema": "memoryflow/1.0",
        "collector_id": "collector-a",
        "collector_seq": 1,
        "event_id": "evt",
        "obs_time": "2026-01-03T01:00:00.000Z",
        "skew_budget_ms": 200,
    }
    payloads = {
        "MOS_DECLARE": {"entry_id": "e"},
        "MEM_WRITE": {
            "entry_id": "e",
            "content_digest": "sha256:abc",
            "update_id": "u",
            "weight": {"num": "1", "den": "1"},
            "ttl_ms": 1,
            "risk_level": 0,
        },
        "MEM_REPLACE": {"old_entry_id": "e1", "new_entry_id": "e2"},
        "MEM_DELETE": {"entry_id": "e"},
        "MEM_READ": {
            "entry_id": "e",
            "content_digest": "sha256:abc",
            "update_id": "u",
            "request_id": "r",
        },
        "MEM_USE": {
            "entry_id": "e",
            "content_digest": "sha256:abc",
            "update_id": "u",
            "request_id": "r",
        },
        "MEM_VERIFY": {
            "target_entry_id": "e",
            "target_digest": "sha256:abc",
            "target_update_id": "u",
            "verdict": "PASS",
            "verifier_id": "v",
        },
        "MEM_CORRECT": {
            "target_entry_id": "e",
            "target_digest": "sha256:abc",
            "target_update_id": "u",
            "corrected_digest": "sha256:def",
            "corrected_update_id": "u2",
            "correction_class": "factual",
        },
    }

    for index, (event_type, payload) in enumerate(payloads.items(), start=1):
        event = (
            base
            | {"event_type": event_type, "collector_seq": index, "event_id": f"evt-{index}"}
            | payload
        )
        parsed, diagnostics = validate_record(event, line=index)
        assert parsed is not None, event_type
        assert diagnostics == [], event_type
