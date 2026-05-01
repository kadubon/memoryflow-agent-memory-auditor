# Event Schema

MemoryFlow consumes JSONL: one JSON object per line. Every event has a common
envelope plus event-specific fields.

## Common Envelope

Required fields:

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | Must be `memoryflow/1.0` |
| `event_type` | string | One of the eight core event types |
| `collector_id` | string | Stable id for the collector/emitter |
| `collector_seq` | integer | Strictly increasing per collector |
| `event_id` | string | Globally unique id for deduplication |
| `obs_time` | RFC3339 string | Collector observation time used for ordering |
| `skew_budget_ms` | nonnegative integer | Declared skew tolerance |

Compatibility behavior: missing or empty `event_id` is accepted by the parser so
the verifier can emit a diagnostic. It cannot be deduplicated. `P0` fails closed;
`P1` and `P2` downgrade affected comparability.

Optional common fields:

| Field | Meaning |
|---|---|
| `event_time` | Source-side event time; compared against `obs_time` when present |
| `extensions` | Object reserved for non-core metadata |
| `canon_alg` | Digest canonicalization declaration |
| `hash_alg` | Hash algorithm declaration |

## Strict JSON

The validator rejects:

- blank JSONL lines
- non-object JSONL records
- non-standard constants such as `NaN` and `Infinity`
- duplicate object keys
- excessive nesting
- oversized lines
- unknown fields outside `extensions`

## Event Types

| Event | Required payload | Notes |
|---|---|---|
| `MOS_DECLARE` | `entry_id` | Declares an entry before strict operations |
| `MEM_WRITE` | `entry_id`, `content_digest`, `update_id`, `weight`, `ttl_ms`, `risk_level` | Effective if `update_id` changes |
| `MEM_REPLACE` | `old_entry_id`, `new_entry_id` | Supersedes `old_entry_id` |
| `MEM_DELETE` | `entry_id` | Tombstones an active entry |
| `MEM_READ` | `entry_id`, `content_digest`, `update_id`, `request_id` | P2 may allow missing version binding as unknown-version |
| `MEM_USE` | `entry_id`, `content_digest`, `update_id`, `request_id` | Stronger than read for uptake |
| `MEM_VERIFY` | `target_entry_id`, `target_digest`, `target_update_id`, `verdict`, `verifier_id` | `verdict` is `PASS` or `FAIL` |
| `MEM_CORRECT` | `target_entry_id`, `target_digest`, `target_update_id`, `corrected_digest`, `corrected_update_id`, `correction_class` | Declares a corrected version; effective as a mutation only when the target is current |

## Rational Values

Rational fields use exact integer strings:

```json
{"num": "3", "den": "10"}
```

The verifier uses exact rational arithmetic for normative weighted metrics. Do
not emit JSON floats for weights.

## Ordering And Deduplication

The normative order key is:

```text
(obs_time, collector_id, collector_seq, event_id)
```

Duplicate `event_id` values are deduplicated after deterministic sorting; later
duplicates are no-ops. If two kept events collide on the full order key,
MemoryFlow emits `ORDER_KEY_COLLISION`. P0 fails closed. P1/P2 use a
field-derived deterministic tiebreaker only to avoid arrival-order dependence;
strict comparability is not claimed.

Input lines do not need to arrive pre-sorted. A batch verifier sorts by the
normative order key and may emit `INPUT_NOT_IN_DETERMINISTIC_ORDER` as
informational telemetry; this does not by itself degrade audit validity.

## Capped Reads

When a collector reports multiple `MEM_READ` events for the same `request_id` and
declares a `cap`, every read in that group must declare the same `cap` and a
nonnegative `rank`. The selected set is the first `cap` reads sorted by:

```text
(rank, entry_id, update_id)
```

Unselected reads receive `READ_TRUNCATED_BY_CAP` and do not inflate read uptake,
zombie read counts, or superseded read counts.

## Minimal Valid Trace

```jsonl
{"schema":"memoryflow/1.0","event_type":"MOS_DECLARE","collector_id":"c","collector_seq":1,"event_id":"e1","obs_time":"2026-01-03T01:00:00.000Z","skew_budget_ms":0,"entry_id":"m1"}
{"schema":"memoryflow/1.0","event_type":"MEM_WRITE","collector_id":"c","collector_seq":2,"event_id":"e2","obs_time":"2026-01-03T01:00:01.000Z","skew_budget_ms":0,"entry_id":"m1","content_digest":"sha256:abc","update_id":"u1","weight":{"num":"1","den":"1"},"ttl_ms":1000,"risk_level":1}
{"schema":"memoryflow/1.0","event_type":"MEM_USE","collector_id":"c","collector_seq":3,"event_id":"e3","obs_time":"2026-01-03T01:00:02.000Z","skew_budget_ms":0,"entry_id":"m1","content_digest":"sha256:abc","update_id":"u1","request_id":"r1"}
```
