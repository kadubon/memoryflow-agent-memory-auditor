import json
from pathlib import Path

from memoryflow.profiles import ConformanceProfile
from memoryflow.verifier import AuditConfig, audit_jsonl_file


def test_large_jsonl_validation_and_audit_smoke(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    for index in range(200):
        seq = index + 1
        events.append(
            {
                "schema": "memoryflow/1.0",
                "event_type": "MOS_DECLARE",
                "collector_id": "collector-a",
                "collector_seq": seq,
                "event_id": f"evt-{seq:04d}",
                "obs_time": f"2026-01-03T01:{index // 60:02d}:{index % 60:02d}.000Z",
                "skew_budget_ms": 0,
                "entry_id": f"entry-{seq:04d}",
            }
        )
    path = tmp_path / "large.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    cert = audit_jsonl_file(path, config=AuditConfig(profile=ConformanceProfile.P0))

    data = cert.to_dict()
    assert data["status"] == "VALID"
    assert data["counters"]["events_processed"] == 200
    assert len(data["entries"]) == 200

