"""MemoryFlow conformance profile names."""

from enum import StrEnum


class ConformanceProfile(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


__all__ = ["ConformanceProfile"]
