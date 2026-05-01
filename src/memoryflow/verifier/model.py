"""State and certificate models for profile-aware MemoryFlow auditing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from memoryflow.metrics import MetricResult
from memoryflow.rational import Rational
from memoryflow.status import ResultStatus


class EntryLifecycle(StrEnum):
    UNKNOWN = "Unknown"
    ACTIVE = "Active"
    TOMBSTONED = "Tombstoned"
    SUPERSEDED = "Superseded"


class ObligationStatus(StrEnum):
    PENDING = "PENDING"
    SATISFIED = "SATISFIED"
    EXPIRED = "EXPIRED"
    INVALIDATED_BY_MUTATION = "INVALIDATED_BY_MUTATION"


@dataclass
class EntryState:
    entry_id: str
    state: EntryLifecycle = EntryLifecycle.UNKNOWN
    gen: int = 0
    last_touch_ms: int | None = None
    ttl_ms: int | None = None
    risk_level: int | None = None
    weight: Rational | None = None
    current_digest: str | None = None
    current_update_id: str | None = None
    delete_ms: int | None = None
    sup_ms: int | None = None
    stale_integral_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "state": self.state.value,
            "gen": self.gen,
            "last_touch_ms": self.last_touch_ms,
            "ttl_ms": self.ttl_ms,
            "risk_level": self.risk_level,
            "weight": self.weight.to_json() if self.weight is not None else None,
            "current_digest": self.current_digest,
            "current_update_id": self.current_update_id,
            "delete_ms": self.delete_ms,
            "sup_ms": self.sup_ms,
        }


@dataclass
class VerificationObligation:
    entry_id: str
    update_id: str
    target_digest: str
    write_ms: int
    gen: int
    deadline_ms: int | None = None
    has_provenance: bool = False
    status: ObligationStatus = ObligationStatus.PENDING
    satisfied_ms: int | None = None
    expired_ms: int | None = None
    invalidated_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "update_id": self.update_id,
            "target_digest": self.target_digest,
            "write_ms": self.write_ms,
            "gen": self.gen,
            "deadline_ms": self.deadline_ms,
            "has_provenance": self.has_provenance,
            "status": self.status.value,
            "satisfied_ms": self.satisfied_ms,
            "expired_ms": self.expired_ms,
            "invalidated_ms": self.invalidated_ms,
        }


@dataclass(frozen=True)
class AuditCertificate:
    tool_version: str
    profile: str
    status: ResultStatus
    input_sha256: str | None
    config: dict[str, Any]
    validation: dict[str, Any]
    counters: dict[str, int]
    metrics: list[MetricResult]
    entries: list[EntryState]
    obligations: list[VerificationObligation]
    diagnostics: list[dict[str, Any]]
    theory_semantics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_version": self.tool_version,
            "profile": self.profile,
            "status": self.status.value,
            "input_sha256": self.input_sha256,
            "config": self.config,
            "validation": self.validation,
            "counters": dict(sorted(self.counters.items())),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "entries": [entry.to_dict() for entry in self.entries],
            "obligations": [obligation.to_dict() for obligation in self.obligations],
            "diagnostics": self.diagnostics,
            "theory_semantics": self.theory_semantics,
        }
