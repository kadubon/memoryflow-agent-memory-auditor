# MemoryFlow Agent Memory Auditor

[![CI](https://github.com/kadubon/memoryflow-agent-memory-auditor/actions/workflows/ci.yml/badge.svg)](https://github.com/kadubon/memoryflow-agent-memory-auditor/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

MemoryFlow audits the dynamic memory of LLM agents from declared event streams.
It answers operational questions such as:

- Did the agent use newly written memory?
- Did it keep using stale, deleted, or superseded memory?
- Were memory updates verified before their deadlines?
- Were failed verifications corrected in time?
- Which metrics are valid, degraded, non-comparable, or not computable?

MemoryFlow is not a memory algorithm, vector database, embedding model, retrieval
strategy, prompt template, or tracing dashboard. It is a telemetry verifier: it
measures what your agent declares through deterministic memory events, and it
does not claim to know hidden truth inside your memory store.

Research basis: Takahashi, K. (2026). MemoryFlow: Real-Time,
Implementation-Agnostic Telemetry for Measuring Dynamic Memory Quality in LLM
Agents. Zenodo. https://doi.org/10.5281/zenodo.18136347

## Why This Exists

Most agent observability tells you that retrieval happened. MemoryFlow checks
whether the declared memory behavior is auditable:

- deterministic event ordering, independent of arrival order
- exact rational arithmetic for weighted metrics
- profile-specific fail-closed or downgrade behavior
- version binding with `entry_id`, `update_id`, and `content_digest`
- explicit statuses instead of silently reporting weak telemetry as valid
- no dependency on any LLM framework, vector DB, memory store, or network service

Use it as a local CLI, a CI check, a library module, or an adapter layer inside a
larger evaluation system.

## Who Should Use This

Use MemoryFlow if you build or evaluate LLM agents with mutable memory and need
to know whether memory writes, reads, uses, deletions, replacements,
verification failures, and corrections are auditable from declared telemetry.

Do not use MemoryFlow as a memory store, retrieval engine, embedding system, or
truth oracle.

## How This Differs From Agent Memory Systems

MemoryFlow does not store, retrieve, summarize, or rank memories. Systems such
as vector-memory stores, graph-memory systems, or long-term agent memory
frameworks decide what should be remembered. MemoryFlow asks a different
question: whether the memory behavior declared by such systems is auditable,
version-bound, stale-aware, correction-aware, and comparable under a declared
conformance profile.

## Install

For end users after package publication:

```bash
pip install memoryflow-agent-memory-auditor
pip install "memoryflow-agent-memory-auditor[server]"
pipx install memoryflow-agent-memory-auditor
```

For installation directly from GitHub before a package release:

```bash
pip install "memoryflow-agent-memory-auditor @ git+https://github.com/kadubon/memoryflow-agent-memory-auditor.git"
```

For contributors, this project uses `uv`.

```bash
uv sync --all-extras
uv run memoryflow --version
```

For optional local HTTP endpoints:

```bash
uv sync --extra server
```

The core package has no runtime dependencies and performs no outbound network
calls.

## Five-Minute Audit

Create a sample trace, validate it, and audit it:

```bash
uv run memoryflow sample --case stale-memory --out events.jsonl
uv run memoryflow validate events.jsonl
uv run memoryflow audit events.jsonl --profile P1 --format json
uv run memoryflow audit events.jsonl --profile P1 --out report.html
```

Initialize a project-local config and examples:

```bash
uv run memoryflow init
uv run memoryflow audit memoryflow-examples/stale-memory.jsonl \
  --config .memoryflow/config.json \
  --format json
```

Read a compact score:

```bash
uv run memoryflow score events.jsonl --profile P1 --format json
```

Compare two traces:

```bash
uv run memoryflow diff before.jsonl after.jsonl --profile P1
```

Explain a JSON report:

```bash
uv run memoryflow explain-report report.json
```

## What You Emit

MemoryFlow consumes JSONL. Each line is one memory event. The eight core event
types are:

- `MOS_DECLARE`
- `MEM_WRITE`
- `MEM_REPLACE`
- `MEM_DELETE`
- `MEM_READ`
- `MEM_USE`
- `MEM_VERIFY`
- `MEM_CORRECT`

Every event uses this envelope:

```json
{
  "schema": "memoryflow/1.0",
  "event_type": "MEM_WRITE",
  "collector_id": "agent-runtime",
  "collector_seq": 1,
  "event_id": "evt-0001",
  "obs_time": "2026-01-03T01:00:00.000Z",
  "skew_budget_ms": 200
}
```

Example write:

```json
{
  "schema": "memoryflow/1.0",
  "event_type": "MEM_WRITE",
  "collector_id": "agent-runtime",
  "collector_seq": 2,
  "event_id": "evt-0002",
  "obs_time": "2026-01-03T01:00:01.000Z",
  "skew_budget_ms": 200,
  "entry_id": "customer-fact-17",
  "content_digest": "sha256:abc123",
  "update_id": "u-001",
  "weight": {"num": "1", "den": "1"},
  "ttl_ms": 300000,
  "risk_level": 2
}
```

Rational values are exact JSON objects such as `{"num": "3", "den": "10"}`.
Normative metrics do not use floating point arithmetic.

## Pick A Profile

Use the weakest profile that matches the telemetry you can actually emit.

| Profile | Use When | Behavior |
|---|---|---|
| `P0` | You need strict, comparable audit evidence | Fails closed on missing declares, missing version bindings, skew/order violations, and impossible transitions |
| `P1` | You run production agents and can emit update ids and digests | Allows implicit creation on `MEM_WRITE`, but downgrades comparability where needed |
| `P2` | You are onboarding legacy logs | Allows unknown-version `MEM_READ`; version-sensitive metrics become `BEST_EFFORT` or `NONCOMPARABLE` |

## What You Get

Audit output is a stable JSON certificate plus optional HTML report. Results use
explicit statuses:

| Status | Meaning |
|---|---|
| `VALID` | Required telemetry and bindings support the result |
| `INVALID` | Fail-closed condition; do not trust the affected result |
| `DEGRADED` | Computed with a known comparability limitation |
| `NONCOMPARABLE` | Value exists, but should not be compared across systems/runs |
| `NOT_COMPUTABLE` | Required config or telemetry is absent |
| `BEST_EFFORT` | Legacy or P2 result with explicit limitations |

Implemented metrics include churn, uptake, read-uptake, correction latency,
verified/proven write fractions (VUF/PUF), staleness/risk exposure, zombie
exposure, and supersedence exposure.

## Use As A Python Module

The public API is small and stable enough for other tools and AI agents to call.

```python
from pathlib import Path

from memoryflow import AuditConfig, ConformanceProfile, audit_jsonl_file

certificate = audit_jsonl_file(
    Path("events.jsonl"),
    config=AuditConfig(
        profile=ConformanceProfile.P1,
        uptake_horizon_ms=300_000,
        correction_horizon_ms=300_000,
        verify_deadline_ms=300_000,
    ),
)

data = certificate.to_dict()
print(data["status"])
print({metric["name"]: metric["status"] for metric in data["metrics"]})
```

Emit events locally:

```python
from memoryflow.adapters import MemoryFlowEmitter, write_jsonl

events = []
emitter = MemoryFlowEmitter("agent-runtime", skew_budget_ms=200, sink=events.append)

emitter.mos_declare("customer-fact-17")
emitter.mem_write(
    "customer-fact-17",
    content_digest="sha256:abc123",
    update_id="u-001",
    weight={"num": "1", "den": "1"},
    ttl_ms=300000,
    risk_level=2,
)

write_jsonl(events, "events.jsonl")
```

The verifier is independent from the CLI, reports, adapters, dashboard, server,
LLM frameworks, and vector stores.

## Optional Integrations

MemoryFlow provides dependency-light helpers:

- plain JSONL reader/writer
- Python SDK emitter
- OpenTelemetry dictionary converter
- OpenInference-style custom attributes
- LangChain/LangGraph-style duck-typed callback helper
- SQLite example adapter
- generic vector metadata helper
- optional FastAPI/Uvicorn local server

Run the optional local server:

```bash
uv run --extra server memoryflow serve --host 127.0.0.1 --port 8765
```

Server mode is unauthenticated and intended for local or private trusted
networks. The CLI prints a warning if you bind to a non-loopback host.

## What MemoryFlow Cannot Prove

MemoryFlow verifies declared telemetry only. It cannot prove:

- that the underlying memory store did not mutate without emitting events
- that an implementer did not fabricate structurally valid events
- that an external provenance source is true
- that a risk level is semantically correct beyond the declared risk map

This boundary is intentional. MemoryFlow makes unsupported claims explicit
instead of filling gaps with inference.

## Documentation

- [Documentation index](docs/index.md)
- [Event schema](docs/event-schema.md)
- [Conformance profiles](docs/conformance-profiles.md)
- [Metrics](docs/metrics.md)
- [Configuration](docs/configuration.md)
- [Integrations and module API](docs/integrations.md)
- [Theory-to-code mapping](docs/theory-to-code.md)
- [Security guide](docs/security.md)

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy src/memoryflow
uv run bandit -q -r src/memoryflow
uv build
```

License: Apache-2.0.
