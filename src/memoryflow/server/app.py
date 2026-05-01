"""Optional FastAPI app factory for local MemoryFlow audit services."""

# mypy: disable-error-code="untyped-decorator"

from __future__ import annotations

import importlib
import json
import tempfile
from pathlib import Path
from typing import Any

from memoryflow import __version__
from memoryflow.profiles import ConformanceProfile
from memoryflow.schema import validate_jsonl_file
from memoryflow.verifier import AuditConfig, audit_jsonl_file

DEFAULT_MAX_BODY_BYTES = 5 * 1024 * 1024


def create_app(*, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> Any:
    """Create a FastAPI app without making FastAPI a core dependency."""

    if isinstance(max_body_bytes, bool) or max_body_bytes < 0:
        raise ValueError("max_body_bytes must be a nonnegative integer")

    try:
        fastapi = importlib.import_module("fastapi")
        responses = importlib.import_module("fastapi.responses")
    except ImportError as exc:
        message = "install memoryflow-agent-memory-auditor[server] to use server mode"
        raise RuntimeError(message) from exc

    app = fastapi.FastAPI(title="MemoryFlow Auditor", version=__version__)
    body_param = fastapi.Body(..., media_type="application/x-ndjson")
    http_exception = fastapi.HTTPException
    json_response = responses.JSONResponse

    def body_to_temp_path(body: bytes) -> Path:
        if len(body) > max_body_bytes:
            raise http_exception(status_code=413, detail="request body exceeds size limit")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            detail = "request body must be UTF-8 JSONL"
            raise http_exception(status_code=400, detail=detail) from exc
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".jsonl",
            delete=False,
        )
        try:
            with handle:
                handle.write(text)
        except OSError as exc:
            raise http_exception(status_code=500, detail="could not persist request body") from exc
        return Path(handle.name)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "memoryflow_version": __version__,
            "core_network_calls": False,
        }

    @app.post("/validate")
    async def validate(body: bytes = body_param) -> Any:
        path = body_to_temp_path(body)
        try:
            report = validate_jsonl_file(path)
            return json_response(report.to_dict(include_events=True))
        finally:
            _unlink(path)

    @app.post("/audit")
    async def audit(
        body: bytes = body_param,
        profile: str = "P1",
        uptake_horizon_ms: int | None = None,
        correction_horizon_ms: int | None = None,
        verify_deadline_ms: int | None = None,
    ) -> Any:
        try:
            conformance_profile = ConformanceProfile(profile)
        except ValueError as exc:
            raise http_exception(status_code=400, detail="profile must be P0, P1, or P2") from exc
        path = body_to_temp_path(body)
        try:
            try:
                config = AuditConfig(
                    profile=conformance_profile,
                    uptake_horizon_ms=uptake_horizon_ms,
                    correction_horizon_ms=correction_horizon_ms,
                    verify_deadline_ms=verify_deadline_ms,
                )
            except ValueError as exc:
                raise http_exception(status_code=400, detail=str(exc)) from exc
            certificate = audit_jsonl_file(
                path,
                config=config,
            )
            return json_response(certificate.to_dict())
        finally:
            _unlink(path)

    return app


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def certificate_json_response(certificate: dict[str, Any]) -> str:
    """Return deterministic JSON for simple static dashboard embedding."""

    return json.dumps(certificate, indent=2, sort_keys=True)
