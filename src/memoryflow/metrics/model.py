"""Metric result model with explicit validity/comparability status."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memoryflow.status import ResultStatus


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: ResultStatus
    value: Any = None
    unit: str | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "status": self.status.value,
            "value": self.value,
        }
        if self.unit is not None:
            data["unit"] = self.unit
        if self.reasons:
            data["reasons"] = self.reasons
        return data

