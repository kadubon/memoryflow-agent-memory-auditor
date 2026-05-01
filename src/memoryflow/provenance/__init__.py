"""Declared provenance structure checks for PUF.

These checks validate declared structure only. They do not assert that external
sources are truthful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memoryflow.security.digest import CanonicalizationError, content_digest


@dataclass(frozen=True)
class ProvenanceValidation:
    valid: bool
    reason: str | None = None


def validate_provenance(value: Any) -> ProvenanceValidation:
    if not isinstance(value, dict):
        return ProvenanceValidation(False, "provenance must be an object")
    kind = value.get("kind")
    if not isinstance(kind, str) or not kind:
        return ProvenanceValidation(False, "provenance.kind must be a nonempty string")
    if kind == "hash_link":
        structure = _require_strings(value, "hash_alg", "source_uri", "source_digest")
        if not structure.valid:
            return structure
        return _validate_hash_link_content(value)
    if kind == "signature":
        return _require_strings(
            value,
            "signature_alg",
            "signature",
            "public_key_id",
            "signed_digest",
        )
    return ProvenanceValidation(
        False,
        "provenance.kind must be hash_link or signature for comparable PUF",
    )


def _require_strings(value: dict[str, Any], *fields: str) -> ProvenanceValidation:
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field]:
            return ProvenanceValidation(False, f"provenance.{field} must be a nonempty string")
    return ProvenanceValidation(True)


def _validate_hash_link_content(value: dict[str, Any]) -> ProvenanceValidation:
    if "source_content" not in value:
        return ProvenanceValidation(True)
    try:
        computed = content_digest(value["source_content"], hash_alg=value["hash_alg"])
    except CanonicalizationError as exc:
        return ProvenanceValidation(False, str(exc))
    if computed != value["source_digest"]:
        return ProvenanceValidation(
            False,
            "provenance.source_digest does not match canonical source_content digest",
        )
    return ProvenanceValidation(True)


__all__ = ["ProvenanceValidation", "validate_provenance"]
