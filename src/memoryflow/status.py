"""Result status values reserved for verifier and metric outputs."""

from enum import StrEnum


class ResultStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    DEGRADED = "DEGRADED"
    NONCOMPARABLE = "NONCOMPARABLE"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"
    BEST_EFFORT = "BEST_EFFORT"
