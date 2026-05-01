# MemoryFlow Documentation

MemoryFlow is a telemetry-only verifier for dynamic memory quality in LLM
agents. It is designed for engineers who need to audit agent memory behavior
without adopting a specific memory algorithm, vector database, embedding model,
retrieval method, or prompting strategy.

Start here:

1. Read the README and run the sample audit.
2. Check [Event Schema](event-schema.md) to emit valid JSONL.
3. Pick a profile in [Conformance Profiles](conformance-profiles.md).
4. Interpret results with [Metrics](metrics.md).
5. Use the library or adapters from [Integrations and Module API](integrations.md).
6. Map the paper to implementation with [Theory To Code](theory-to-code.md).

## Mental Model

MemoryFlow watches declared memory events:

```text
agent/store -> MemoryFlow JSONL events -> validator -> verifier -> metrics/report
```

It does not query your memory store, call an LLM, compute embeddings, or infer
unreported operations. If the event stream cannot support a metric, the result is
`INVALID`, `DEGRADED`, `NONCOMPARABLE`, `NOT_COMPUTABLE`, or `BEST_EFFORT`.

## Main Entry Points

- CLI: `memoryflow validate`, `memoryflow audit`, `memoryflow score`,
  `memoryflow diff`, `memoryflow explain-report`
- Python API: `memoryflow.audit_jsonl_file`, `memoryflow.validate_jsonl_lines`
- Event emitter: `memoryflow.adapters.MemoryFlowEmitter`
- Reports: stable JSON certificates and static escaped HTML

## Proof Boundary

MemoryFlow can detect declared stale memory, zombie references, superseded
references, failed verification obligations, missing bindings, ordering
problems, and profile violations. It cannot prove hidden memory-store behavior,
external source truth, or the honesty of a malicious telemetry emitter.
