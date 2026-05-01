# Conformance Profiles

Profiles control how strictly MemoryFlow treats missing or inconsistent
telemetry. They do not change the meaning of the paper's metrics; they decide
whether a result is valid, invalid, downgraded, non-comparable, or best-effort.

## Which Profile Should I Use?

| Profile | Best For | Required Telemetry | Result Behavior |
|---|---|---|---|
| `P0` | Formal strict audits, CI gates, cross-system comparison | `MOS_DECLARE` before operations, full version bindings, unique ordering fields, skew compliance | Fail closed on violations |
| `P1` | Production operational telemetry | `content_digest` and `update_id` on read/use/verify/correct | Continue where deterministic, but downgrade comparability |
| `P2` | Legacy or partial logs | Entry ids plus as much version binding as available | Unknown-version reads become best-effort/non-comparable |

## P0 Strict Audit

P0 rejects:

- read/use/write/replace/delete before `MOS_DECLARE`
- implicit creation except none
- missing version binding on read/use/verify/correct
- skew violations
- collector sequence violations
- order-key collisions
- impossible state transitions
- update-id/digest rebinding
- digest-sensitive events with `canon_alg="none"`
- undeclared risk levels when a risk map is configured

Use P0 when you want the certificate to fail rather than silently compare weak
telemetry.

## P1 Operational

P1 allows implicit creation on `MEM_WRITE` because many systems can instrument
writes before they can emit `MOS_DECLARE`. It still requires version binding on
read/use/verify/correct. It does not implicitly create entries from
`MEM_CORRECT`, `MEM_DELETE`, or `MEM_REPLACE`.

Typical P1 outcomes:

- `IMPLICIT_CREATE_ON_WRITE` -> `DEGRADED`
- collector ordering comparability issue -> `DEGRADED`
- skew issue -> `DEGRADED`
- invalid structure -> `INVALID`

Use P1 for production rollout and CI trend tracking while instrumentation is
maturing.

## P2 Best-Effort

P2 allows `MEM_READ` without `content_digest` and `update_id`. Such reads are
treated as unknown-version. Version-sensitive read uptake becomes
`NONCOMPARABLE`; the run may be `BEST_EFFORT`.

P2 never silently upgrades unsupported metrics to valid. Use it to start from
legacy logs without pretending they support strict audit semantics.

## Status Propagation

MemoryFlow emits diagnostics with machine-readable codes and a result status in
the diagnostic context where applicable. Severe P0 failures make the certificate
`INVALID`. P1/P2 downgrade only when deterministic processing remains possible.
Structural parse/schema errors remain invalid because the verifier cannot know
which memory operation was omitted or altered.
