import json
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st

from memoryflow.schema import validate_jsonl_file


def _event(seq: int, obs_second: int, event_id: str) -> dict[str, object]:
    return {
        "schema": "memoryflow/1.0",
        "event_type": "MOS_DECLARE",
        "collector_id": "collector-a",
        "collector_seq": seq,
        "event_id": event_id,
        "obs_time": f"2026-01-03T01:00:{obs_second:02d}.000Z",
        "skew_budget_ms": 0,
        "entry_id": f"entry-{seq}",
    }


BASE_EVENTS = [_event(1, 1, "evt-1"), _event(2, 2, "evt-2"), _event(3, 3, "evt-3")]


@given(st.permutations(BASE_EVENTS))
@settings(max_examples=12)
def test_validation_order_is_independent_of_arrival_order(
    events: tuple[dict[str, object], ...],
) -> None:
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "events.jsonl"
        path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

        report = validate_jsonl_file(path)

    assert [event.event_id for event in report.events] == ["evt-1", "evt-2", "evt-3"]


@given(st.permutations([_event(1, 1, "dup"), _event(2, 2, "dup"), _event(3, 3, "evt-3")]))
@settings(max_examples=6)
def test_dedup_keeps_first_by_deterministic_order(
    events: tuple[dict[str, object], ...],
) -> None:
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "events.jsonl"
        path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

        report = validate_jsonl_file(path)

    assert report.duplicate_count == 1
    assert [event.event_id for event in report.events] == ["dup", "evt-3"]
    assert report.events[0].envelope.collector_seq == 1
