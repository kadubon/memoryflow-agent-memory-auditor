"""Dependency-free LangChain/LangGraph-style hook helpers."""

from __future__ import annotations

from typing import Any

from memoryflow.adapters.emitter import MemoryFlowEmitter


class MemoryFlowCallback:
    """Duck-typed callback helper for projects that use LangChain-style hooks."""

    def __init__(self, emitter: MemoryFlowEmitter) -> None:
        self.emitter = emitter

    def on_retriever_end(
        self,
        documents: list[Any],
        *,
        request_id: str,
        **_: Any,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index, document in enumerate(documents):
            metadata = getattr(document, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            entry_id = metadata.get("memoryflow.entry_id")
            update_id = metadata.get("memoryflow.update_id")
            content_digest = metadata.get("memoryflow.content_digest")
            if not isinstance(entry_id, str):
                continue
            if not isinstance(update_id, str):
                continue
            if not isinstance(content_digest, str):
                continue
            events.append(
                self.emitter.mem_read(
                    entry_id,
                    request_id=request_id,
                    content_digest=content_digest,
                    update_id=update_id,
                    rank=index,
                    cap=len(documents),
                )
            )
        return events
