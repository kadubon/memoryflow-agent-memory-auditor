# Configuration

`memoryflow init` creates `.memoryflow/config.json` and a sample trace:

```bash
uv run memoryflow init
```

Use the config during audit:

```bash
uv run memoryflow audit events.jsonl --config .memoryflow/config.json --format json
```

Command-line flags override config values.

## Example

```json
{
  "schema": "memoryflow/config/1.0",
  "profile": "P1",
  "uptake_horizon_ms": 300000,
  "correction_horizon_ms": 300000,
  "verify_deadline_ms": 300000,
  "risk_weight_map": {
    "0": 0,
    "1": 1,
    "2": 5,
    "3": 20
  }
}
```

## Fields

| Field | Type | Used By |
|---|---|---|
| `profile` | `P0`, `P1`, `P2` | verifier semantics |
| `uptake_horizon_ms` | positive integer | `uptake`, `read_uptake` |
| `correction_horizon_ms` | nonnegative integer | `fraction_uncorrected_within_horizon` |
| `verify_deadline_ms` | nonnegative integer | `vuf`, `puf`, obligation expiry |
| `risk_weight_map` | object string-int -> int | risk exposure |
| `risk_weight` | currently `identity` | compatibility with init output |

Absent horizons make dependent metrics `NOT_COMPUTABLE` rather than guessed.
Unknown keys are rejected so misspelled fields cannot silently disable a metric
in production or CI.

## Safety

Config files are plain JSON and are never executed. Invalid config types raise a
configuration error before audit begins.
