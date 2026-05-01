"""Canonical JSON bytes and content digests for MemoryFlow bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


class CanonicalizationError(ValueError):
    """Raised when content cannot be canonicalized deterministically."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic canonical JSON bytes for MemoryFlow content.

    This is a conservative JCS-style subset for Python data: object keys are
    sorted, insignificant whitespace is removed, and floats are rejected because
    cross-runtime numeric canonicalization is easy to get wrong.
    """

    _validate_json_value(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_digest(value: Any, *, hash_alg: str = "sha256", prefix: bool = True) -> str:
    canonical = canonical_json_bytes(value)
    try:
        digest = hashlib.new(hash_alg)
    except ValueError as exc:
        raise CanonicalizationError(f"unsupported hash algorithm: {hash_alg}") from exc
    digest.update(canonical)
    hexdigest = digest.hexdigest()
    return f"{hash_alg}:{hexdigest}" if prefix else hexdigest


def _validate_json_value(value: Any) -> None:
    if isinstance(value, float):
        raise CanonicalizationError("floating point JSON values are not canonicalized")
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise CanonicalizationError("JSON integers outside the I-JSON safe range are rejected")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("JSON object keys must be strings")
            _validate_json_value(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json_value(child)
