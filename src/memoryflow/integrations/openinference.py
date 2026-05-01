"""OpenInference-style custom attribute mapping for MemoryFlow fields."""

from __future__ import annotations

from typing import Any

ENTRY_ID_ATTR = "memoryflow.entry_id"
UPDATE_ID_ATTR = "memoryflow.update_id"
CONTENT_DIGEST_ATTR = "memoryflow.content_digest"


def memoryflow_binding_attributes(
    *,
    entry_id: str,
    update_id: str,
    content_digest: str,
) -> dict[str, Any]:
    return {
        ENTRY_ID_ATTR: entry_id,
        UPDATE_ID_ATTR: update_id,
        CONTENT_DIGEST_ATTR: content_digest,
    }


def extract_memoryflow_binding(attributes: dict[str, Any]) -> dict[str, str] | None:
    entry_id = attributes.get(ENTRY_ID_ATTR)
    update_id = attributes.get(UPDATE_ID_ATTR)
    content_digest = attributes.get(CONTENT_DIGEST_ATTR)
    if not isinstance(entry_id, str):
        return None
    if not isinstance(update_id, str):
        return None
    if not isinstance(content_digest, str):
        return None
    return {
        "entry_id": entry_id,
        "update_id": update_id,
        "content_digest": content_digest,
    }
