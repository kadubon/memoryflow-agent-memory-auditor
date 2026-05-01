"""Exact rational values with JSON parsing for MemoryFlow fields."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from types import NotImplementedType
from typing import Any

from memoryflow.constants import MAX_RATIONAL_DIGITS


class RationalError(ValueError):
    """Raised when a MemoryFlow rational value is malformed."""


def _parse_int_string(value: Any, *, field_name: str) -> int:
    if not isinstance(value, str):
        raise RationalError(f"{field_name} must be a decimal integer string")
    signless = value[1:] if value.startswith("-") else value
    if not signless or not signless.isdigit():
        raise RationalError(f"{field_name} must be a decimal integer string")
    if len(signless) > MAX_RATIONAL_DIGITS:
        raise RationalError(f"{field_name} exceeds {MAX_RATIONAL_DIGITS} digits")
    return int(value)


@dataclass(frozen=True)
class Rational:
    """A normalized rational number backed by Python integers."""

    num: int
    den: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.num, bool) or isinstance(self.den, bool):
            raise RationalError("rational numerator and denominator must be integers")
        if not isinstance(self.num, int) or not isinstance(self.den, int):
            raise RationalError("rational numerator and denominator must be integers")
        if self.den == 0:
            raise RationalError("rational denominator must not be zero")

        num = self.num
        den = self.den
        if den < 0:
            num = -num
            den = -den
        factor = gcd(abs(num), den)
        object.__setattr__(self, "num", num // factor)
        object.__setattr__(self, "den", den // factor)

    @classmethod
    def from_json(cls, value: Any, *, field_name: str = "rational") -> Rational:
        if not isinstance(value, dict):
            raise RationalError(f"{field_name} must be an object with num and den strings")
        if set(value) != {"num", "den"}:
            raise RationalError(f"{field_name} must contain only num and den")
        num = _parse_int_string(value["num"], field_name=f"{field_name}.num")
        den = _parse_int_string(value["den"], field_name=f"{field_name}.den")
        return cls(num, den)

    def to_json(self) -> dict[str, str]:
        return {"num": str(self.num), "den": str(self.den)}

    def require_nonnegative(self, *, field_name: str = "rational") -> Rational:
        if self.num < 0:
            raise RationalError(f"{field_name} must be nonnegative")
        return self

    def __add__(self, other: Rational) -> Rational:
        return Rational(self.num * other.den + other.num * self.den, self.den * other.den)

    def __sub__(self, other: Rational) -> Rational:
        return Rational(self.num * other.den - other.num * self.den, self.den * other.den)

    def __mul__(self, other: Rational | int) -> Rational | NotImplementedType:
        if isinstance(other, int) and not isinstance(other, bool):
            return Rational(self.num * other, self.den)
        if isinstance(other, Rational):
            return Rational(self.num * other.num, self.den * other.den)
        return NotImplemented

    def __truediv__(self, other: Rational | int) -> Rational | NotImplementedType:
        if isinstance(other, int) and not isinstance(other, bool):
            return Rational(self.num, self.den * other)
        if isinstance(other, Rational):
            return Rational(self.num * other.den, self.den * other.num)
        return NotImplemented

    def __str__(self) -> str:
        return f"{self.num}/{self.den}"
