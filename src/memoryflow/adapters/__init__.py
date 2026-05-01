"""Dependency-light adapters for emitting and transporting MemoryFlow events."""

from memoryflow.adapters.emitter import MemoryFlowEmitter
from memoryflow.adapters.jsonl import dumps_event, read_jsonl_records, write_jsonl
from memoryflow.adapters.vector import attach_memoryflow_metadata, memoryflow_metadata

__all__ = [
    "MemoryFlowEmitter",
    "attach_memoryflow_metadata",
    "dumps_event",
    "memoryflow_metadata",
    "read_jsonl_records",
    "write_jsonl",
]
