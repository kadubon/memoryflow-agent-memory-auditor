"""Structured diagnostics emitted by validators and ordering checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DiagnosticSeverity(StrEnum):
    """Severity levels used in machine-readable validation output."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Diagnostic:
    """A line-addressable diagnostic for event streams."""

    severity: DiagnosticSeverity
    code: str
    message: str
    line: int | None = None
    event_id: str | None = None
    field: str | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
        }
        if self.line is not None:
            data["line"] = self.line
        if self.event_id is not None:
            data["event_id"] = self.event_id
        if self.field is not None:
            data["field"] = self.field
        if self.context is not None:
            data["context"] = self.context
        return data


def has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)
