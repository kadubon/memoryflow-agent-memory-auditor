"""Safe JSON configuration loading for MemoryFlow audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from memoryflow.profiles import ConformanceProfile
from memoryflow.verifier import AuditConfig

CONFIG_FIELDS = {
    "schema",
    "profile",
    "uptake_horizon_ms",
    "correction_horizon_ms",
    "verify_deadline_ms",
    "risk_weight",
    "risk_weight_map",
    "note",
}


def load_audit_config(path: Path | str) -> AuditConfig:
    """Load an audit config from JSON without executing user-controlled code."""

    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid MemoryFlow config JSON at line {exc.lineno}: {exc.msg}") from exc
    except OSError as exc:
        message = exc.strerror or exc.__class__.__name__
        raise ValueError(f"could not read MemoryFlow config: {message}") from exc
    if not isinstance(data, dict):
        raise ValueError("MemoryFlow config must be a JSON object")
    unknown_fields = sorted(set(data) - CONFIG_FIELDS)
    if unknown_fields:
        joined = ", ".join(unknown_fields)
        raise ValueError(f"unknown MemoryFlow config field(s): {joined}")
    schema = data.get("schema")
    if schema is not None and schema != "memoryflow/config/1.0":
        raise ValueError("config field schema must be memoryflow/config/1.0")

    profile = _optional_profile(data.get("profile"))
    return AuditConfig(
        profile=profile or ConformanceProfile.P1,
        uptake_horizon_ms=_optional_nonnegative_int(data, "uptake_horizon_ms"),
        correction_horizon_ms=_optional_nonnegative_int(data, "correction_horizon_ms"),
        verify_deadline_ms=_optional_nonnegative_int(data, "verify_deadline_ms"),
        risk_weight_map=_optional_risk_weight_map(data),
    )


def merge_audit_config(
    base: AuditConfig,
    *,
    profile: ConformanceProfile | None = None,
    uptake_horizon_ms: int | None = None,
    correction_horizon_ms: int | None = None,
    verify_deadline_ms: int | None = None,
    risk_weight_map: dict[int, int] | None = None,
) -> AuditConfig:
    """Return a new config with explicit command-line overrides applied."""

    return AuditConfig(
        profile=profile or base.profile,
        uptake_horizon_ms=(
            uptake_horizon_ms if uptake_horizon_ms is not None else base.uptake_horizon_ms
        ),
        correction_horizon_ms=(
            correction_horizon_ms
            if correction_horizon_ms is not None
            else base.correction_horizon_ms
        ),
        verify_deadline_ms=(
            verify_deadline_ms if verify_deadline_ms is not None else base.verify_deadline_ms
        ),
        risk_weight_map=risk_weight_map if risk_weight_map is not None else base.risk_weight_map,
    )


def _optional_profile(value: Any) -> ConformanceProfile | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("config field profile must be a string")
    try:
        return ConformanceProfile(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ConformanceProfile)
        raise ValueError(f"config field profile must be one of: {allowed}") from exc


def _optional_nonnegative_int(data: dict[str, Any], field: str) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"config field {field} must be a nonnegative integer")
    return int(value)


def _optional_risk_weight_map(data: dict[str, Any]) -> dict[int, int] | None:
    if data.get("risk_weight") == "identity" and "risk_weight_map" not in data:
        return None
    value = data.get("risk_weight_map")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("config field risk_weight_map must be an object")
    result: dict[int, int] = {}
    for raw_key, raw_weight in value.items():
        if not isinstance(raw_key, str) or not raw_key.isdecimal():
            raise ValueError("risk_weight_map keys must be nonnegative integer strings")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, int) or raw_weight < 0:
            raise ValueError("risk_weight_map values must be nonnegative integers")
        result[int(raw_key)] = raw_weight
    return result
