"""Informative claim digest helpers.

The MemoryFlow paper keeps the normative core entry-primary. Claim digests are
an optional interoperability helper for systems that group multiple entry
versions into one higher-level fact.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from memoryflow.security.digest import CanonicalizationError, canonical_json_bytes


@dataclass(frozen=True)
class ClaimMember:
    entry_id: str
    update_id: str
    content_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "entry_id": self.entry_id,
            "update_id": self.update_id,
            "content_digest": self.content_digest,
        }


def claim_digest(
    members: Iterable[ClaimMember | Mapping[str, Any]],
    *,
    hash_alg: str = "sha256",
    prefix: bool = True,
) -> str:
    """Compute H(JCS(sorted member list)) for declared claim membership."""

    normalized = [_normalize_member(member) for member in members]
    normalized.sort(key=lambda item: (item.entry_id, item.update_id, item.content_digest))
    payload = [member.to_dict() for member in normalized]
    try:
        digest = hashlib.new(hash_alg)
    except ValueError as exc:
        raise CanonicalizationError(f"unsupported hash algorithm: {hash_alg}") from exc
    digest.update(canonical_json_bytes(payload))
    hexdigest = digest.hexdigest()
    return f"{hash_alg}:{hexdigest}" if prefix else hexdigest


def _normalize_member(member: ClaimMember | Mapping[str, Any]) -> ClaimMember:
    if isinstance(member, ClaimMember):
        _require_nonempty(member.entry_id, "entry_id")
        _require_nonempty(member.update_id, "update_id")
        _require_nonempty(member.content_digest, "content_digest")
        return member
    entry_id = member.get("entry_id")
    update_id = member.get("update_id")
    content_digest = member.get("content_digest")
    _require_nonempty(entry_id, "entry_id")
    _require_nonempty(update_id, "update_id")
    _require_nonempty(content_digest, "content_digest")
    return ClaimMember(
        entry_id=str(entry_id),
        update_id=str(update_id),
        content_digest=str(content_digest),
    )


def _require_nonempty(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise CanonicalizationError(f"claim member {field} must be a nonempty string")


__all__ = ["ClaimMember", "claim_digest"]
