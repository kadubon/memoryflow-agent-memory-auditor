"""Dataclasses for parsed MemoryFlow events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    MOS_DECLARE = "MOS_DECLARE"
    MEM_WRITE = "MEM_WRITE"
    MEM_REPLACE = "MEM_REPLACE"
    MEM_DELETE = "MEM_DELETE"
    MEM_READ = "MEM_READ"
    MEM_USE = "MEM_USE"
    MEM_VERIFY = "MEM_VERIFY"
    MEM_CORRECT = "MEM_CORRECT"


@dataclass(frozen=True)
class EventEnvelope:
    schema: str
    event_type: EventType
    collector_id: str
    collector_seq: int
    event_id: str | None
    obs_time: str
    obs_time_ms: int
    skew_budget_ms: int
    event_time: str | None = None
    event_time_ms: int | None = None

    @property
    def order_key(self) -> tuple[int, str, int, str]:
        return (
            self.obs_time_ms,
            self.collector_id,
            self.collector_seq,
            self.event_id or "",
        )


@dataclass(frozen=True)
class MemoryFlowEvent:
    envelope: EventEnvelope
    payload: dict[str, Any]
    line: int
    raw: dict[str, Any]

    @property
    def event_id(self) -> str | None:
        return self.envelope.event_id

    @property
    def event_type(self) -> EventType:
        return self.envelope.event_type

    @property
    def order_key(self) -> tuple[int, str, int, str]:
        return self.envelope.order_key
