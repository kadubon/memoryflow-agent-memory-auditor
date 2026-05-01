"""Deterministic JSONL samples for CLI and tests."""

from __future__ import annotations

import json
from typing import Any


def list_sample_cases() -> list[str]:
    return ["stale-memory"]


def get_sample_events(case: str) -> list[dict[str, Any]]:
    if case != "stale-memory":
        raise ValueError(f"unknown sample case: {case}")
    return [
        {
            "schema": "memoryflow/1.0",
            "event_type": "MOS_DECLARE",
            "collector_id": "collector-a",
            "collector_seq": 1,
            "event_id": "evt-0001",
            "obs_time": "2026-01-03T01:00:00.000Z",
            "skew_budget_ms": 200,
            "entry_id": "entry-001",
        },
        {
            "schema": "memoryflow/1.0",
            "event_type": "MEM_WRITE",
            "collector_id": "collector-a",
            "collector_seq": 2,
            "event_id": "evt-0002",
            "obs_time": "2026-01-03T01:00:01.000Z",
            "skew_budget_ms": 200,
            "entry_id": "entry-001",
            "content_digest": "sha256:11111111111111111111111111111111",
            "update_id": "update-001",
            "weight": {"num": "1", "den": "1"},
            "ttl_ms": 1000,
            "risk_level": 2,
        },
        {
            "schema": "memoryflow/1.0",
            "event_type": "MEM_READ",
            "collector_id": "collector-a",
            "collector_seq": 3,
            "event_id": "evt-0003",
            "obs_time": "2026-01-03T01:00:03.000Z",
            "skew_budget_ms": 200,
            "entry_id": "entry-001",
            "content_digest": "sha256:11111111111111111111111111111111",
            "update_id": "update-001",
            "request_id": "request-001",
        },
    ]


def get_sample_jsonl(case: str) -> str:
    lines = [
        json.dumps(event, sort_keys=True, separators=(",", ":"))
        for event in get_sample_events(case)
    ]
    return "\n".join(lines) + "\n"
