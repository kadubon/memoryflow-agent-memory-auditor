# Theory To Code Mapping

This page maps the MemoryFlow paper's theoretical concepts to the implementation
modules in this repository. Normative behavior is kept in the core package; CLI,
reports, server, and adapters are convenience layers.

## Normative Core Boundary

Core modules:

- `memoryflow.events`
- `memoryflow.schema`
- `memoryflow.ordering`
- `memoryflow.profiles`
- `memoryflow.rational`
- `memoryflow.verifier`
- `memoryflow.metrics`
- `memoryflow.provenance`
- `memoryflow.security`

Non-core convenience layers:

- `memoryflow.cli`
- `memoryflow.reports`
- `memoryflow.adapters`
- `memoryflow.integrations`
- `memoryflow.server`
- `memoryflow.dashboard`

The core verifier performs no network calls and does not depend on a memory
algorithm, LLM framework, vector database, telemetry SDK, dashboard, or server.

## Concept Mapping

| Paper Concept | Code | Behavior |
|---|---|---|
| Telemetry-only measurement | `memoryflow.verifier`, `memoryflow.schema` | Processes declared events only; no hidden memory operations are inferred |
| Agent | emitter/adapters caller | External system that declares events |
| Memory store | adapter boundary | No required store implementation |
| Collector | `collector_id`, `collector_seq`, `MemoryFlowEmitter` | Per-collector sequence is checked |
| Event stream | `validate_jsonl_file`, `validate_jsonl_lines` | Strict JSONL ingestion with line diagnostics |
| Common envelope | `EventEnvelope` | `schema`, `event_type`, `collector_id`, `collector_seq`, `event_id`, `obs_time`, `skew_budget_ms` |
| Deterministic order | `memoryflow.ordering.sort_events` | Orders by `(obs_time, collector_id, collector_seq, event_id)` and diagnoses collisions |
| Deduplication | `memoryflow.ordering.deduplicate_events` | Keeps first event in deterministic order; later duplicate `event_id` events are no-ops |
| Entry | `EntryState` | Tracks lifecycle, generation, current digest/update, weight, TTL, risk, delete/supersedence times |
| Entry lifecycle | `EntryLifecycle` | `Unknown`, `Active`, `Tombstoned`, `Superseded` |
| Update identity | `update_bindings`, obligations | Binds `(entry_id, update_id)` to `content_digest` |
| Content digest | schema fields, `memoryflow.security.content_digest` | Required where version binding is required |
| Exact rational arithmetic | `memoryflow.rational.Rational` | No floats for normative weighted metrics |
| Conformance profile | `ConformanceProfile`, `AuditConfig.profile` | P0 fail-closed, P1 operational downgrade, P2 best-effort |
| Verification obligation | `VerificationObligation` | Tracks target digest, deadline, status, satisfaction/expiry/invalidation |
| Obligation expiry | obligation deadline heap in verifier | Expires pending verification obligations without scanning all obligations per event |
| VUF | `_vuf_metric` | Fraction of obligated effective `MEM_WRITE` events satisfied by PASS before the deadline and before the next update-changing write/correction |
| PUF | `_puf_metric`, `memoryflow.provenance` | VUF plus valid provenance declared on the counted write |
| Provenance object | `validate_provenance` | Structural checks; embedded hash-linked content can be digest-checked |
| Correction latency | `_VerificationFailureRecord`, `_correction_latency_metric` | FAIL to later matching `MEM_CORRECT`, matched by target version even if the correction is not a current-state mutation |
| Uptake horizon | `_WriteRecord`, `_uptake_metric` | Effective `MEM_WRITE` used/read within configured horizon |
| Staleness boundary | boundary heap in verifier | Uses `last_touch_ms + ttl_ms` for Active entries |
| Right-continuity | exposure helpers | State interpreted at `t+`; instantaneous stale uses the paper's strict `t - last_touch > ttl` rule, while the following open interval is integrated as stale |
| Staleness exposure | `_exposure_metrics` | Instantaneous and time-integrated exact rational weighted exposure |
| Risk exposure | `_risk_weight`, `_exposure_metrics` | Uses declared risk map or identity implementation choice |
| Zombie exposure | `_record_terminal_reference`, exposure accumulators | Reads/uses after delete; delay distribution and integrated exposure |
| Supersedence exposure | `_record_terminal_reference`, exposure accumulators | Reads/uses after replace; delay distribution and integrated exposure |
| Capped read selection | `_build_read_selection_plan` | Selects first `cap` by `(rank, entry_id, update_id)` |
| Validity/comparability status | `ResultStatus`, `MetricResult` | `VALID`, `INVALID`, `DEGRADED`, `NONCOMPARABLE`, `NOT_COMPUTABLE`, `BEST_EFFORT` |
| Audit certificate | `AuditCertificate` | Stable machine-readable JSON result |
| Informative claim digest | `memoryflow.security.claim_digest` | Optional `H(JCS(sorted member list))`; not part of normative entry-primary metrics |

## Profile Semantics In Code

| Profile Rule | Implementation |
|---|---|
| P0 requires `MOS_DECLARE` before memory operations | `_on_write`, `_on_delete`, `_on_reference`, `_on_correct`, `_on_replace` diagnostics |
| P0 no implicit creation | P0 `P0_MISSING_DECLARE` and unknown-entry checks |
| P1 allows implicit creation on `MEM_WRITE` | `_on_write` emits `IMPLICIT_CREATE_ON_WRITE` and continues |
| P1 still requires version binding on read/use/verify/correct | schema validation and `_on_reference`, `_on_verify`, `_on_correct` |
| References must bind to a declared current version | `_on_reference` rejects mismatched or unknown current `update_id`/`content_digest` for uptake-sensitive matching |
| P2 allows missing read version binding | `allow_p2_read_without_version` and `P2_UNKNOWN_VERSION_READ` |
| P2 version-sensitive read uptake not comparable | `_uptake_metric` returns `NONCOMPARABLE` for unknown-version reads |
| Skew violations fail/downgrade by profile | `_check_skew` |
| Impossible transitions fail/downgrade by profile | state-machine handlers in `verifier.core` |

## Implementation Choices Beyond The Paper

These are engineering choices, not new theoretical claims:

| Choice | Reason |
|---|---|
| `TTL_INF_MS = 9223372036854775807` | Practical finite sentinel for infinite TTL |
| Identity default risk weighting | Allows lightweight use when no custom map is declared |
| Strict rejection of duplicate JSON keys | Avoids ambiguous parser-dependent telemetry |
| Field-derived tiebreaker after `ORDER_KEY_COLLISION` | Keeps P1/P2 processing arrival-order independent while downgrading comparability |
| Corrected-version obligations are reported but excluded from VUF/PUF | Preserves the paper's write-denominator metric while exposing corrected-version verification state |
| Signature provenance is structural only | The paper does not define a mandatory key/signature verification scheme |
| Optional FastAPI server | Convenience layer outside the normative verifier |

## Where To Extend

- New event transport: add an adapter in `memoryflow.adapters` or
  `memoryflow.integrations`, then convert to core JSONL events.
- New report format: consume `AuditCertificate.to_dict()`; do not depend on
  verifier internals.
- New dashboard: read report JSON; keep it outside `memoryflow.verifier`.
- New framework hook: use `MemoryFlowEmitter` or emit plain dictionaries.
- New metric: add a `MetricResult` builder in `memoryflow.verifier.core` and
  tests that pin status behavior under P0/P1/P2.
