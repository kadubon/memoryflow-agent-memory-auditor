import json
from pathlib import Path

from memoryflow.diagnostics import DiagnosticSeverity
from memoryflow.ordering import deduplicate_events, sort_events
from memoryflow.schema import validate_jsonl_file, validate_record
from memoryflow.testing.samples import get_sample_events


def _event(raw: dict[str, object]):
    parsed, diagnostics = validate_record(raw)
    assert diagnostics == []
    assert parsed is not None
    return parsed


def test_sort_events_uses_normative_order_key() -> None:
    first = get_sample_events("stale-memory")[0]
    second = get_sample_events("stale-memory")[1]
    first["obs_time"] = "2026-01-03T01:00:02.000Z"
    second["obs_time"] = "2026-01-03T01:00:01.000Z"

    ordered = sort_events([_event(first), _event(second)])

    assert [event.event_id for event in ordered] == ["evt-0002", "evt-0001"]


def test_deduplicate_keeps_first_in_deterministic_order() -> None:
    later = get_sample_events("stale-memory")[0]
    earlier = get_sample_events("stale-memory")[1]
    later["event_id"] = "dup"
    earlier["event_id"] = "dup"
    later["obs_time"] = "2026-01-03T01:00:02.000Z"
    earlier["obs_time"] = "2026-01-03T01:00:01.000Z"

    kept, diagnostics, duplicate_count = deduplicate_events([_event(later), _event(earlier)])

    assert duplicate_count == 1
    assert [event.envelope.collector_seq for event in kept] == [2]
    assert [item.code for item in diagnostics] == ["DUPLICATE_EVENT_ID"]
    assert diagnostics[0].severity is DiagnosticSeverity.INFO


def test_collector_seq_monotonicity_error(tmp_path: Path) -> None:
    events = get_sample_events("stale-memory")
    events[1]["collector_seq"] = 3
    events[2]["collector_seq"] = 2
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    report = validate_jsonl_file(path)

    assert not report.is_valid
    assert any(item.code == "COLLECTOR_SEQ_NOT_STRICTLY_INCREASING" for item in report.diagnostics)


def test_input_order_warning(tmp_path: Path) -> None:
    events = get_sample_events("stale-memory")
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event) for event in reversed(events)) + "\n",
        encoding="utf-8",
    )

    report = validate_jsonl_file(path)

    diagnostic = next(
        item for item in report.diagnostics if item.code == "INPUT_NOT_IN_DETERMINISTIC_ORDER"
    )
    assert diagnostic.severity is DiagnosticSeverity.INFO


def test_order_key_collision_is_reported_and_not_arrival_order_dependent(
    tmp_path: Path,
) -> None:
    first = {
        "schema": "memoryflow/1.0",
        "event_type": "MOS_DECLARE",
        "collector_id": "collector-a",
        "collector_seq": 1,
        "obs_time": "2026-01-03T01:00:01.000Z",
        "skew_budget_ms": 0,
        "entry_id": "entry-a",
    }
    second = first | {"entry_id": "entry-b"}

    forward = tmp_path / "forward.jsonl"
    reverse = tmp_path / "reverse.jsonl"
    forward.write_text("\n".join(json.dumps(event) for event in [first, second]), encoding="utf-8")
    reverse.write_text("\n".join(json.dumps(event) for event in [second, first]), encoding="utf-8")

    forward_report = validate_jsonl_file(forward)
    reverse_report = validate_jsonl_file(reverse)

    assert any(item.code == "ORDER_KEY_COLLISION" for item in forward_report.diagnostics)
    assert any(item.code == "ORDER_KEY_COLLISION" for item in reverse_report.diagnostics)
    assert [event.payload["entry_id"] for event in forward_report.events] == [
        event.payload["entry_id"] for event in reverse_report.events
    ]
