from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from memoryflow.adapters import MemoryFlowEmitter, dumps_event, read_jsonl_records, write_jsonl
from memoryflow.adapters.sqlite import SQLiteMemoryFlowAdapter
from memoryflow.adapters.vector import attach_memoryflow_metadata, memoryflow_metadata
from memoryflow.integrations.langchain import MemoryFlowCallback
from memoryflow.integrations.openinference import (
    extract_memoryflow_binding,
    memoryflow_binding_attributes,
)
from memoryflow.integrations.otel import memoryflow_event_to_otel_log, otel_log_to_memoryflow_event


def _fixed_clock() -> datetime:
    return datetime(2026, 1, 3, 1, 0, 0, 123000, tzinfo=UTC)


def test_top_level_exports_are_usable_for_agent_tools() -> None:
    import memoryflow

    assert memoryflow.AuditConfig is not None
    assert memoryflow.ConformanceProfile.P0.value == "P0"
    assert callable(memoryflow.validate_jsonl_lines)
    assert callable(memoryflow.audit_jsonl_file)


def test_emitter_creates_local_event_without_network() -> None:
    captured: list[dict[str, object]] = []
    emitter = MemoryFlowEmitter(
        "collector-a",
        skew_budget_ms=200,
        clock=_fixed_clock,
        sink=captured.append,
    )

    event = emitter.mos_declare("entry-001")

    assert event["collector_seq"] == 1
    assert event["obs_time"] == "2026-01-03T01:00:00.123Z"
    assert captured == [event]


def test_jsonl_helpers_roundtrip() -> None:
    buffer = io.StringIO()
    events = [{"schema": "memoryflow/1.0", "event_type": "MOS_DECLARE", "entry_id": "e"}]

    count = write_jsonl(events, buffer)

    assert count == 1
    buffer.seek(0)
    assert list(read_jsonl_records(buffer)) == events


def test_jsonl_helpers_reject_nonstandard_constants_and_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="non-standard JSON constant"):
        list(read_jsonl_records(io.StringIO('{"bad": NaN}\n')))

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        list(read_jsonl_records(io.StringIO('{"a": 1, "a": 2}\n')))


def test_jsonl_dumps_rejects_nonstandard_float_values() -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        dumps_event({"bad": float("nan")})


def test_otel_mapping_roundtrip() -> None:
    event = {
        "schema": "memoryflow/1.0",
        "event_type": "MOS_DECLARE",
        "collector_id": "collector-a",
        "collector_seq": 1,
        "event_id": "evt-1",
        "obs_time": "2026-01-03T01:00:00.000Z",
        "skew_budget_ms": 0,
        "entry_id": "entry-001",
    }

    record = memoryflow_event_to_otel_log(event)
    restored = otel_log_to_memoryflow_event(record)

    assert restored == event


def test_openinference_binding_attributes() -> None:
    attrs = memoryflow_binding_attributes(
        entry_id="entry-001",
        update_id="update-001",
        content_digest="sha256:aaa",
    )

    assert extract_memoryflow_binding(attrs) == {
        "entry_id": "entry-001",
        "update_id": "update-001",
        "content_digest": "sha256:aaa",
    }


def test_vector_metadata_helpers() -> None:
    metadata = memoryflow_metadata(
        entry_id="entry-001",
        update_id="update-001",
        content_digest="sha256:aaa",
    )

    attach_memoryflow_metadata(
        metadata,
        entry_id="entry-002",
        update_id="update-002",
        content_digest="sha256:bbb",
    )

    assert metadata["memoryflow.entry_id"] == "entry-002"


@dataclass
class _Document:
    metadata: dict[str, object]


def test_langchain_style_callback_uses_metadata_without_dependency() -> None:
    emitter = MemoryFlowEmitter("collector-a", clock=_fixed_clock)
    callback = MemoryFlowCallback(emitter)
    documents = [
        _Document(
            metadata={
                "memoryflow.entry_id": "entry-001",
                "memoryflow.update_id": "update-001",
                "memoryflow.content_digest": "sha256:aaa",
            }
        )
    ]

    events = callback.on_retriever_end(documents, request_id="request-001")

    assert events[0]["event_type"] == "MEM_READ"
    assert events[0]["rank"] == 0
    assert events[0]["cap"] == 1


def test_sqlite_adapter_emits_write_read_delete() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    emitter = MemoryFlowEmitter("collector-a", clock=_fixed_clock)
    adapter = SQLiteMemoryFlowAdapter(connection, emitter)
    adapter.ensure_table()

    write_event = adapter.write_entry(
        "entry-001",
        content="hello",
        content_digest="sha256:aaa",
        update_id="update-001",
        weight={"num": "1", "den": "1"},
        ttl_ms=1000,
        risk_level=1,
    )
    row, read_event = adapter.read_entry("entry-001", request_id="request-001")
    delete_event = adapter.delete_entry("entry-001")

    assert write_event["event_type"] == "MEM_WRITE"
    assert row is not None
    assert read_event is not None
    assert read_event["event_type"] == "MEM_READ"
    assert delete_event["event_type"] == "MEM_DELETE"
