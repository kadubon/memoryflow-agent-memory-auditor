import json
from pathlib import Path

import pytest

from memoryflow.config import load_audit_config
from memoryflow.profiles import ConformanceProfile
from memoryflow.verifier import AuditConfig


def test_load_audit_config_with_declared_risk_map(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "profile": "P0",
                "uptake_horizon_ms": 1000,
                "correction_horizon_ms": 2000,
                "verify_deadline_ms": 3000,
                "risk_weight_map": {"0": 0, "3": 10},
            }
        ),
        encoding="utf-8",
    )

    config = load_audit_config(path)

    assert config.profile is ConformanceProfile.P0
    assert config.uptake_horizon_ms == 1000
    assert config.correction_horizon_ms == 2000
    assert config.verify_deadline_ms == 3000
    assert config.risk_weight_map == {0: 0, 3: 10}


def test_load_audit_config_rejects_unsafe_types(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"verify_deadline_ms": True}), encoding="utf-8")

    with pytest.raises(ValueError, match="verify_deadline_ms"):
        load_audit_config(path)


def test_load_audit_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"verify_deadline_mss": 1000}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown MemoryFlow config field"):
        load_audit_config(path)


def test_audit_config_rejects_negative_programmatic_horizon() -> None:
    with pytest.raises(ValueError, match="uptake_horizon_ms"):
        AuditConfig(uptake_horizon_ms=-1)


def test_audit_config_accepts_profile_string_for_programmatic_callers() -> None:
    assert AuditConfig(profile="P0").profile is ConformanceProfile.P0  # type: ignore[arg-type]


def test_audit_config_rejects_zero_uptake_horizon() -> None:
    with pytest.raises(ValueError, match="uptake_horizon_ms must be positive"):
        AuditConfig(uptake_horizon_ms=0)


def test_load_audit_config_read_error_does_not_embed_local_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(ValueError) as exc_info:
        load_audit_config(missing)

    assert str(missing) not in str(exc_info.value)
