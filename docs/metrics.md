# Metrics

Every metric is returned as a `MetricResult` with:

- `name`
- `status`
- `value`
- optional `unit`
- optional `reasons`

Statuses are part of the API. A missing or weak telemetry condition must not be
hidden behind a numeric value.

| Status | Meaning |
|---|---|
| `VALID` | Required fields, bindings, ordering, and profile conditions support the result |
| `INVALID` | Fail-closed condition; do not trust the affected result |
| `DEGRADED` | Computed with an explicit limitation |
| `NONCOMPARABLE` | Value exists but should not be compared across systems/runs |
| `NOT_COMPUTABLE` | Required configuration or denominator is absent |
| `BEST_EFFORT` | Legacy/P2-compatible result with explicit limitations |

## Implemented Metrics

| Metric | What It Measures | Main Requirements |
|---|---|---|
| `churn_rate` | Effective mutation activity per millisecond | Nonzero audit window |
| `uptake` | Effective writes used by `MEM_USE` within horizon | `uptake_horizon_ms`, version-bound uses |
| `read_uptake` | Weaker read-based uptake | `uptake_horizon_ms`, version-bound reads |
| `correction_latency` | Delay from `MEM_VERIFY FAIL` to matching `MEM_CORRECT` | Version-bound verify/correct |
| `fraction_uncorrected_within_horizon` | Matured failed verifications not corrected in time | `correction_horizon_ms` |
| `vuf` | Verified write fraction | `verify_deadline_ms`, matching PASS before deadline/update-changing mutation |
| `puf` | Proven write fraction | VUF plus valid write-bound provenance |
| `instantaneous_staleness_exposure` | Current stale weighted exposure | weight, TTL, touch time |
| `staleness_exposure_integral` | Time-integrated stale exposure | complete boundaries |
| `instantaneous_risk_exposure` | Current risk-weighted stale exposure | risk map/default |
| `risk_exposure_integral` | Time-integrated risk-weighted exposure | complete boundaries |
| `zombie_count` | Reads/uses after delete | delete time and later references |
| `zombie_delay` | Delay distribution for zombie references | same |
| `zombie_exposure_integral` | Time-integrated tombstoned exposure | pre-delete weight |
| `supersedence_count` | Reads/uses after replace | supersedence time and later references |
| `supersedence_delay` | Delay distribution for superseded references | same |
| `supersedence_exposure_integral` | Time-integrated superseded exposure | pre-replace weight |

## Effective Mutations

MemoryFlow resists no-op event spam:

- `MEM_WRITE` and current-target `MEM_CORRECT` are effective only if they change
  `update_id` for the entry.
- `MEM_DELETE` is effective only for `Active -> Tombstoned`.
- `MEM_REPLACE` is effective only for `Active -> Superseded` on `old_entry_id`.

## Correction Latency

Correction latency matches a `MEM_VERIFY FAIL` to a later `MEM_CORRECT` by
`target_entry_id`, `target_update_id`, and `target_digest`. This matching is
separate from whether the correction can still be applied as a current-state
mutation. For example, a correction after an intervening write, delete, or
replace can still close the failed-verification latency record, while the
state-machine violation is reported as `DEGRADED` or `INVALID` by profile.

## VUF And PUF

The verifier creates obligations for content-changing versions:

- effective `MEM_WRITE`
- effective `MEM_CORRECT` that creates a corrected current version

An obligation is satisfied by a matching `MEM_VERIFY PASS` before
`verify_deadline_ms` and before the next effective `MEM_WRITE` or
current-target `MEM_CORRECT` that changes the entry's `update_id`.
Pending obligations whose deadline has passed inside the audit window are marked
`EXPIRED`.

The paper-defined `vuf` and `puf` metrics use effective `MEM_WRITE` obligations
as their denominator. Corrected-version obligations are still tracked in the
certificate so consumers can inspect whether a correction was later verified, but
they do not enlarge VUF/PUF.

PUF requires VUF satisfaction plus valid provenance declared on the counted
`MEM_WRITE`. Provenance attached only to `MEM_VERIFY` is useful audit metadata,
but it does not make the original write proven. Hash-linked provenance with
embedded `source_content` is checked against its declared `source_digest`.
External source truth remains outside MemoryFlow's proof boundary.

## Uptake

`uptake` is deliberately separate from VUF/PUF. It asks whether an effective
`MEM_WRITE` was used by `MEM_USE` within `(write_time, write_time + H]`.
Corrected versions do not inflate write uptake unless the original write itself
is used.

`read_uptake` is weaker because reading memory is not the same as using it in a
generation or decision.

References with mismatched or undeclared current `update_id`/`content_digest`
bindings do not count for uptake. Under P0 they fail closed; under weaker
profiles the run is downgraded or best-effort rather than silently accepting a
weak binding.

## Exposure

Integrated exposure uses exact rational arithmetic over event boundaries and
staleness boundaries. The implementation follows the paper's right-continuity
convention: state is interpreted at `t+` after processing all events at time `t`.
For instantaneous exposure, an entry is stale only when `t - last_touch_ms >
ttl_ms`; at the exact `last_touch_ms + ttl_ms` boundary it is not counted as
stale. For the integrated exposure, the open interval after that boundary is
counted as stale until the next boundary.

Risk exposure uses `risk_weight_map` when configured. If no map is configured,
the implementation choice is identity weighting for nonnegative integer
`risk_level` values.

## Capped Read Selection

When `MEM_READ` events share `request_id` and declare a `cap`, the selected set
is deterministic: first `cap` after sorting by `(rank, entry_id, update_id)`.
Unselected reads are reported with `READ_TRUNCATED_BY_CAP` and do not inflate
read-based metrics.
