# Integrations And Module API

MemoryFlow is designed so other applications and agents can use only the pieces
they need. The normative verifier does not import an LLM framework, vector
database, telemetry SDK, server framework, dashboard, or network client.

## Stable Public Imports

```python
from memoryflow import (
    AuditConfig,
    ConformanceProfile,
    ResultStatus,
    audit_jsonl_file,
    validate_jsonl_lines,
)
```

Useful subpackages:

| Package | Purpose |
|---|---|
| `memoryflow.schema` | JSONL and event validation |
| `memoryflow.verifier` | profile-aware audit certificates |
| `memoryflow.rational` | exact rational arithmetic |
| `memoryflow.events` | event dataclasses and timestamp parsing |
| `memoryflow.ordering` | deterministic ordering and deduplication |
| `memoryflow.metrics` | metric result model |
| `memoryflow.provenance` | declared provenance checks |
| `memoryflow.security` | canonical digest and claim digest helpers |
| `memoryflow.adapters` | JSONL, emitter, SQLite, vector helpers |
| `memoryflow.integrations` | OTel/OpenInference/LangChain-style helpers |
| `memoryflow.reports` | static escaped HTML report renderer |

## Validate In Memory

```python
from memoryflow import validate_jsonl_lines

lines = [
    '{"schema":"memoryflow/1.0","event_type":"MOS_DECLARE",'
    '"collector_id":"c","collector_seq":1,"event_id":"e1",'
    '"obs_time":"2026-01-03T01:00:00.000Z","skew_budget_ms":0,'
    '"entry_id":"m1"}\n'
]

report = validate_jsonl_lines(lines)
assert report.is_valid
```

## Audit A File

```python
from pathlib import Path

from memoryflow import AuditConfig, ConformanceProfile, audit_jsonl_file

certificate = audit_jsonl_file(
    Path("events.jsonl"),
    config=AuditConfig(profile=ConformanceProfile.P1, verify_deadline_ms=300000),
)
data = certificate.to_dict()
```

## Emit Events Locally

```python
from memoryflow.adapters import MemoryFlowEmitter, write_jsonl

events = []
emitter = MemoryFlowEmitter("collector-a", sink=events.append)
emitter.mos_declare("entry-1")
emitter.mem_write(
    "entry-1",
    content_digest="sha256:abc",
    update_id="u1",
    weight={"num": "1", "den": "1"},
    ttl_ms=1000,
    risk_level=1,
)
write_jsonl(events, "events.jsonl")
```

The emitter has no network sink by default. A caller decides where events go.

## JSONL Helpers

`memoryflow.adapters.write_jsonl` writes deterministic compact JSONL.
`memoryflow.adapters.read_jsonl_records` reads raw JSON objects for converters.
The helper layer rejects non-standard JSON constants, duplicate object keys, and
non-standard float output values such as `NaN`.

## OpenTelemetry And OpenInference

The OTel helpers use plain dictionaries so the core package does not depend on
OpenTelemetry:

```python
from memoryflow.integrations.otel import memoryflow_event_to_otel_log

otel_record = memoryflow_event_to_otel_log(event)
```

OpenInference-style bindings:

```python
from memoryflow.integrations.openinference import memoryflow_binding_attributes

attrs = memoryflow_binding_attributes(
    entry_id="entry-1",
    update_id="u1",
    content_digest="sha256:abc",
)
```

## LangChain/LangGraph-Style Hooks

`memoryflow.integrations.langchain.MemoryFlowCallback` is duck-typed. It reads
metadata fields such as:

- `memoryflow.entry_id`
- `memoryflow.update_id`
- `memoryflow.content_digest`

No LangChain import is required by MemoryFlow.

## Vector Store Metadata

Use `memoryflow.adapters.vector.memoryflow_metadata` or
`attach_memoryflow_metadata` to put version bindings into documents or records
managed by any vector store.

## Optional Server

Server mode is opt-in:

```bash
uv run --extra server memoryflow serve --host 127.0.0.1 --port 8765
```

Endpoints:

- `GET /healthz`
- `POST /validate`
- `POST /audit?profile=P1`

The server is unauthenticated and intended for local or private trusted
networks. The CLI prints a warning when `--host` is not loopback. The server
applies a body-size limit and returns the same stable JSON objects as the
library.

## Claim Digest Helper

Claim-level aggregation is informative, not normative. For systems that group
multiple entry versions into a higher-level fact:

```python
from memoryflow.security import claim_digest

digest = claim_digest([
    {"entry_id": "e1", "update_id": "u1", "content_digest": "sha256:a"},
    {"entry_id": "e2", "update_id": "u3", "content_digest": "sha256:b"},
])
```

This computes `H(JCS(sorted member list))` over declared
`(entry_id, update_id, content_digest)` members.
