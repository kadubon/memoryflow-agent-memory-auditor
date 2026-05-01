"""SQLite memory-store helper that emits MemoryFlow events around local operations."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from memoryflow.adapters.emitter import MemoryFlowEmitter


class SQLiteMemoryFlowAdapter:
    """Minimal SQLite helper for examples and lightweight local memory stores."""

    def __init__(self, connection: sqlite3.Connection, emitter: MemoryFlowEmitter) -> None:
        self.connection = connection
        self.emitter = emitter

    def ensure_table(self, table: str = "memory_entries") -> None:
        _require_default_table(table)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_entries (
                entry_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                update_id TEXT NOT NULL,
                weight_num TEXT NOT NULL,
                weight_den TEXT NOT NULL,
                ttl_ms INTEGER NOT NULL,
                risk_level INTEGER NOT NULL
            )
            """
        )
        self.connection.commit()

    def write_entry(
        self,
        entry_id: str,
        *,
        content: str,
        content_digest: str,
        update_id: str,
        weight: Mapping[str, str],
        ttl_ms: int,
        risk_level: int,
        table: str = "memory_entries",
    ) -> dict[str, Any]:
        _require_default_table(table)
        self.connection.execute(
            """
            INSERT INTO memory_entries
              (entry_id, content, content_digest, update_id, weight_num, weight_den,
               ttl_ms, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              content=excluded.content,
              content_digest=excluded.content_digest,
              update_id=excluded.update_id,
              weight_num=excluded.weight_num,
              weight_den=excluded.weight_den,
              ttl_ms=excluded.ttl_ms,
              risk_level=excluded.risk_level
            """,
            (
                entry_id,
                content,
                content_digest,
                update_id,
                weight["num"],
                weight["den"],
                ttl_ms,
                risk_level,
            ),
        )
        self.connection.commit()
        return self.emitter.mem_write(
            entry_id,
            content_digest=content_digest,
            update_id=update_id,
            weight=dict(weight),
            ttl_ms=ttl_ms,
            risk_level=risk_level,
        )

    def read_entry(
        self,
        entry_id: str,
        *,
        request_id: str,
        table: str = "memory_entries",
    ) -> tuple[sqlite3.Row | tuple[Any, ...] | None, dict[str, Any] | None]:
        _require_default_table(table)
        row = self.connection.execute(
            "SELECT * FROM memory_entries WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None, None
        content_digest = row["content_digest"] if isinstance(row, sqlite3.Row) else row[2]
        update_id = row["update_id"] if isinstance(row, sqlite3.Row) else row[3]
        event = self.emitter.mem_read(
            entry_id,
            request_id=request_id,
            content_digest=content_digest,
            update_id=update_id,
        )
        return row, event

    def delete_entry(self, entry_id: str, *, table: str = "memory_entries") -> dict[str, Any]:
        _require_default_table(table)
        self.connection.execute(
            "DELETE FROM memory_entries WHERE entry_id = ?",
            (entry_id,),
        )
        self.connection.commit()
        return self.emitter.mem_delete(entry_id)


def _require_default_table(value: str) -> None:
    if value != "memory_entries":
        raise ValueError("SQLite adapter currently supports only the memory_entries table")
