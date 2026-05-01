"""Deterministic event ordering rules."""

from __future__ import annotations

import json

from memoryflow.diagnostics import Diagnostic, DiagnosticSeverity
from memoryflow.events import MemoryFlowEvent


def sort_events(events: list[MemoryFlowEvent]) -> list[MemoryFlowEvent]:
    """Sort events by the normative MemoryFlow order key.

    The paper's primary key is ``(obs_time, collector_id, collector_seq, event_id)``.
    For malformed streams where that key collides, use a field-derived canonical
    tiebreaker so internal processing is still arrival-order independent. Such
    collisions are separately diagnosed by ``check_order_key_collisions``.
    """

    return sorted(events, key=_total_order_key)


def check_order_key_collisions(events: list[MemoryFlowEvent]) -> list[Diagnostic]:
    """Report strict-order collisions after deduplication.

    Missing or duplicate order fields can make the normative tuple fail to be a
    total order. P0 must fail closed; weaker profiles may use the canonical
    field-derived tiebreaker but should downgrade comparability.
    """

    diagnostics: list[Diagnostic] = []
    seen: dict[tuple[int, str, int, str], MemoryFlowEvent] = {}
    for event in sort_events(events):
        previous = seen.get(event.order_key)
        if previous is None:
            seen[event.order_key] = event
            continue
        diagnostics.append(
            Diagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="ORDER_KEY_COLLISION",
                message=(
                    "multiple kept events share the same MemoryFlow order key; "
                    "strict total ordering requires unique ordering fields"
                ),
                line=event.line,
                event_id=event.event_id,
                context={
                    "previous_line": previous.line,
                    "order_key": list(event.order_key),
                },
            )
        )
    return diagnostics


def check_input_order(events: list[MemoryFlowEvent]) -> list[Diagnostic]:
    """Report whether input arrival order already matches deterministic order."""

    ordered = sort_events(events)
    if [id(item) for item in events] == [id(item) for item in ordered]:
        return []
    return [
        Diagnostic(
            severity=DiagnosticSeverity.INFO,
            code="INPUT_NOT_IN_DETERMINISTIC_ORDER",
            message=(
                "input order differs from MemoryFlow deterministic order; batch validation "
                "will sort by (obs_time, collector_id, collector_seq, event_id)"
            ),
        )
    ]


def deduplicate_events(
    events: list[MemoryFlowEvent],
) -> tuple[list[MemoryFlowEvent], list[Diagnostic], int]:
    """Deduplicate by event_id after deterministic sorting.

    Missing event_id values are never deduplicated.
    """

    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    kept: list[MemoryFlowEvent] = []
    duplicate_count = 0

    for event in sort_events(events):
        event_id = event.event_id
        if not event_id:
            kept.append(event)
            continue
        if event_id in seen:
            duplicate_count += 1
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.INFO,
                    code="DUPLICATE_EVENT_ID",
                    message="duplicate event_id treated as a no-op after the first event",
                    line=event.line,
                    event_id=event_id,
                    field="event_id",
                )
            )
            continue
        seen.add(event_id)
        kept.append(event)

    return kept, diagnostics, duplicate_count


def check_collector_seq_monotonicity(events: list[MemoryFlowEvent]) -> list[Diagnostic]:
    """Check that collector_seq is strictly increasing per collector in verifier order."""

    diagnostics: list[Diagnostic] = []
    last_seq: dict[str, int] = {}
    last_line: dict[str, int] = {}

    for event in sort_events(events):
        collector_id = event.envelope.collector_id
        collector_seq = event.envelope.collector_seq
        previous = last_seq.get(collector_id)
        if previous is not None and collector_seq <= previous:
            diagnostics.append(
                Diagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="COLLECTOR_SEQ_NOT_STRICTLY_INCREASING",
                    message=(
                        "collector_seq must be strictly increasing per collector in "
                        "deterministic verifier order"
                    ),
                    line=event.line,
                    event_id=event.event_id,
                    field="collector_seq",
                    context={
                        "collector_id": collector_id,
                        "previous_seq": previous,
                        "previous_line": last_line[collector_id],
                        "current_seq": collector_seq,
                    },
                )
            )
        last_seq[collector_id] = collector_seq
        last_line[collector_id] = event.line

    return diagnostics


def _total_order_key(event: MemoryFlowEvent) -> tuple[int, str, int, str, str]:
    return (*event.order_key, _canonical_event_tiebreaker(event))


def _canonical_event_tiebreaker(event: MemoryFlowEvent) -> str:
    return json.dumps(
        event.raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
