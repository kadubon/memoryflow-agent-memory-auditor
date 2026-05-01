"""Generic vector-store metadata helpers without vendor dependencies."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, Protocol


class VectorStoreLike(Protocol):
    """Minimal protocol for wrappers that attach MemoryFlow metadata."""

    def add_texts(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any: ...


def memoryflow_metadata(
    *,
    entry_id: str,
    update_id: str,
    content_digest: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = {
        "memoryflow.entry_id": entry_id,
        "memoryflow.update_id": update_id,
        "memoryflow.content_digest": content_digest,
    }
    if extra is not None:
        metadata.update(extra)
    return metadata


def attach_memoryflow_metadata(
    metadata: MutableMapping[str, Any],
    *,
    entry_id: str,
    update_id: str,
    content_digest: str,
) -> MutableMapping[str, Any]:
    metadata["memoryflow.entry_id"] = entry_id
    metadata["memoryflow.update_id"] = update_id
    metadata["memoryflow.content_digest"] = content_digest
    return metadata

