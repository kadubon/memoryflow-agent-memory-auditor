from pathlib import Path

from memoryflow.schema import validate_jsonl_file


def test_stale_memory_example_validation_golden() -> None:
    report = validate_jsonl_file(Path("examples/jsonl/stale-memory.jsonl"))

    assert report.to_dict(include_events=True) == {
        "valid": True,
        "total_lines": 3,
        "event_count": 3,
        "kept_event_count": 3,
        "duplicate_count": 0,
        "missing_event_id_count": 0,
        "diagnostics": [],
        "events": [
            {
                "line": 1,
                "event_type": "MOS_DECLARE",
                "event_id": "evt-0001",
                "order_key": [1767402000000, "collector-a", 1, "evt-0001"],
            },
            {
                "line": 2,
                "event_type": "MEM_WRITE",
                "event_id": "evt-0002",
                "order_key": [1767402001000, "collector-a", 2, "evt-0002"],
            },
            {
                "line": 3,
                "event_type": "MEM_READ",
                "event_id": "evt-0003",
                "order_key": [1767402003000, "collector-a", 3, "evt-0003"],
            },
        ],
    }

