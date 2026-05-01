"""Generic JSONL helpers for MemoryFlow events."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, NoReturn, TextIO


def dumps_event(event: dict[str, Any]) -> str:
    """Serialize one event deterministically as compact JSON."""

    return json.dumps(
        event,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def write_jsonl(events: Iterable[dict[str, Any]], target: Path | str | TextIO) -> int:
    """Write events to JSONL and return the number of events written."""

    close_after = False
    handle: TextIO
    if isinstance(target, str | Path):
        handle = Path(target).open("w", encoding="utf-8")
        close_after = True
    else:
        handle = target
    count = 0
    try:
        for event in events:
            handle.write(dumps_event(event))
            handle.write("\n")
            count += 1
    finally:
        if close_after:
            handle.close()
    return count


def read_jsonl_records(source: Path | str | TextIO) -> Iterator[dict[str, Any]]:
    """Read raw JSON objects from JSONL without applying MemoryFlow validation."""

    close_after = False
    handle: TextIO
    if isinstance(source, str | Path):
        handle = Path(source).open("r", encoding="utf-8")
        close_after = True
    else:
        handle = source
    try:
        for line in handle:
            if line.strip():
                value = json.loads(
                    line,
                    object_pairs_hook=_object_without_duplicate_keys,
                    parse_constant=_reject_json_constant,
                )
                if not isinstance(value, dict):
                    raise ValueError("JSONL record must be an object")
                yield value
    finally:
        if close_after:
            handle.close()


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key is not allowed: {key}")
        value[key] = child
    return value
