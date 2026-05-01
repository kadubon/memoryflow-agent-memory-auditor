"""OpenTelemetry log/span mapping helpers for MemoryFlow events.

These helpers use plain dictionaries so importing this module does not require
OpenTelemetry packages. Real OTel SDK objects can be adapted by callers.
"""

from __future__ import annotations

from typing import Any

from memoryflow.constants import SCHEMA_VERSION


def memoryflow_event_to_otel_log(event: dict[str, Any]) -> dict[str, Any]:
    attributes = {f"memoryflow.{key}": value for key, value in event.items()}
    return {
        "body": event.get("event_type", "memoryflow.event"),
        "attributes": attributes,
        "observed_time_unix_nano": None,
    }


def otel_log_to_memoryflow_event(record: dict[str, Any]) -> dict[str, Any]:
    attributes = record.get("attributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("OTel record attributes must be an object")
    event: dict[str, Any] = {}
    for key, value in attributes.items():
        if isinstance(key, str) and key.startswith("memoryflow."):
            event[key.removeprefix("memoryflow.")] = value
    if "schema" not in event:
        event["schema"] = SCHEMA_VERSION
    if "event_type" not in event:
        body = record.get("body")
        if isinstance(body, str) and (body.startswith("MEM_") or body == "MOS_DECLARE"):
            event["event_type"] = body
    return event


def convert_otel_jsonl_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [otel_log_to_memoryflow_event(record) for record in records]
