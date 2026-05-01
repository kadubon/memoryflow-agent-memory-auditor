# Security Guide

MemoryFlow validates untrusted JSONL. Its security properties are separate from
the truthfulness of the telemetry source.

## Default Security Posture

- no outbound network calls in the core verifier
- no telemetry collection by the tool
- no API-key collection
- no executable config format
- strict JSONL parsing
- line-size and nesting limits
- exact rational parsing with digit limits
- conservative canonical JSON helpers that reject floats and unsafe-size JSON integers
- deterministic ordering independent of arrival order
- sanitized read/config errors that avoid embedding local absolute paths
- escaped static HTML reports
- optional server body-size limits and loopback-bind warning

## Input Rejections

The validator rejects:

- malformed JSON
- blank lines
- non-object JSONL records
- duplicate JSON object keys
- non-standard constants such as `NaN` and `Infinity`
- floats in rational fields
- enormous integers beyond the project sentinel
- invalid RFC3339 timestamps
- unknown event fields outside `extensions`

## Threat Classes

| Threat | Mitigation |
|---|---|
| Huge JSONL line | line-size limit |
| Deep JSON nesting | nesting limit |
| Integer/rational abuse | integer sentinel and rational digit limit |
| Arrival-order manipulation | deterministic sorting and order-key collision diagnostics |
| Duplicate events | `event_id` deduplication |
| HTML injection | escaped report rendering |
| Unsafe config | JSON-only config, no execution |
| Server exposure | unauthenticated server mode; default loopback bind and warning on non-loopback hosts |
| Server flooding | optional server body-size limit |
| Misleading telemetry | explicit proof boundary and profile statuses |

## What Security Does Not Mean Here

MemoryFlow can fail closed or downgrade when declared telemetry violates its
invariants. It cannot prove that:

- the underlying memory store emitted every operation
- an adversarial implementer did not fabricate valid-looking telemetry
- an external provenance source is true
- a declared risk level is semantically correct

These limits are part of the design. The tool makes unsupported claims visible
instead of inferring hidden memory behavior.
