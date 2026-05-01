import pytest

from memoryflow.rational import Rational, RationalError


def test_rational_from_json_normalizes_and_serializes() -> None:
    value = Rational.from_json({"num": "6", "den": "8"}, field_name="weight")

    assert value == Rational(3, 4)
    assert value.to_json() == {"num": "3", "den": "4"}


def test_rational_rejects_float_shape() -> None:
    with pytest.raises(RationalError, match="object"):
        Rational.from_json(0.3, field_name="weight")


def test_rational_rejects_zero_denominator() -> None:
    with pytest.raises(RationalError, match="denominator"):
        Rational.from_json({"num": "1", "den": "0"}, field_name="weight")


def test_rational_requires_decimal_string_in_json() -> None:
    with pytest.raises(RationalError, match="string"):
        Rational.from_json({"num": 1, "den": "2"}, field_name="weight")


def test_rational_arithmetic_is_exact() -> None:
    assert Rational(1, 3) + Rational(1, 6) == Rational(1, 2)
    assert Rational(3, 10) * 3 == Rational(9, 10)


def test_nonnegative_weight_requirement() -> None:
    with pytest.raises(RationalError, match="nonnegative"):
        Rational(-1, 2).require_nonnegative(field_name="weight")

