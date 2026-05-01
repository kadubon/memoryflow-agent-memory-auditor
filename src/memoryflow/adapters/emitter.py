"""Small Python SDK emitter for MemoryFlow event dictionaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from memoryflow.constants import SCHEMA_VERSION

Clock = Callable[[], datetime]
Sink = Callable[[dict[str, Any]], None]


def utc_now() -> datetime:
    return datetime.now(UTC)


class MemoryFlowEmitter:
    """Create deterministic MemoryFlow event dictionaries for SDK users.

    The emitter does not send telemetry anywhere by default. A caller may provide
    a local sink such as a JSONL writer.
    """

    def __init__(
        self,
        collector_id: str,
        *,
        skew_budget_ms: int = 0,
        clock: Clock = utc_now,
        sink: Sink | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.skew_budget_ms = skew_budget_ms
        self.clock = clock
        self.sink = sink
        self._seq = 0

    @property
    def collector_seq(self) -> int:
        return self._seq

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "schema": SCHEMA_VERSION,
            "event_type": event_type,
            "collector_id": self.collector_id,
            "collector_seq": self._seq,
            "event_id": str(uuid4()),
            "obs_time": _format_rfc3339_ms(self.clock()),
            "skew_budget_ms": self.skew_budget_ms,
            **payload,
        }
        if self.sink is not None:
            self.sink(event)
        return event

    def mos_declare(self, entry_id: str, **payload: Any) -> dict[str, Any]:
        return self.emit("MOS_DECLARE", entry_id=entry_id, **payload)

    def mem_write(
        self,
        entry_id: str,
        *,
        content_digest: str,
        update_id: str,
        weight: dict[str, str],
        ttl_ms: int,
        risk_level: int,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "MEM_WRITE",
            entry_id=entry_id,
            content_digest=content_digest,
            update_id=update_id,
            weight=weight,
            ttl_ms=ttl_ms,
            risk_level=risk_level,
            **payload,
        )

    def mem_read(self, entry_id: str, *, request_id: str, **payload: Any) -> dict[str, Any]:
        return self.emit("MEM_READ", entry_id=entry_id, request_id=request_id, **payload)

    def mem_use(self, entry_id: str, *, request_id: str, **payload: Any) -> dict[str, Any]:
        return self.emit("MEM_USE", entry_id=entry_id, request_id=request_id, **payload)

    def mem_delete(self, entry_id: str, **payload: Any) -> dict[str, Any]:
        return self.emit("MEM_DELETE", entry_id=entry_id, **payload)

    def mem_replace(self, old_entry_id: str, new_entry_id: str, **payload: Any) -> dict[str, Any]:
        return self.emit(
            "MEM_REPLACE",
            old_entry_id=old_entry_id,
            new_entry_id=new_entry_id,
            **payload,
        )

    def mem_verify(
        self,
        *,
        target_entry_id: str,
        target_digest: str,
        target_update_id: str,
        verdict: str,
        verifier_id: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "MEM_VERIFY",
            target_entry_id=target_entry_id,
            target_digest=target_digest,
            target_update_id=target_update_id,
            verdict=verdict,
            verifier_id=verifier_id,
            **payload,
        )

    def mem_correct(
        self,
        *,
        target_entry_id: str,
        target_digest: str,
        target_update_id: str,
        corrected_digest: str,
        corrected_update_id: str,
        correction_class: str,
        **payload: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "MEM_CORRECT",
            target_entry_id=target_entry_id,
            target_digest=target_digest,
            target_update_id=target_update_id,
            corrected_digest=corrected_digest,
            corrected_update_id=corrected_update_id,
            correction_class=correction_class,
            **payload,
        )


def _format_rfc3339_ms(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

