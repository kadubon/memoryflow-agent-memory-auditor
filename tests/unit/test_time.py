import pytest

from memoryflow.events import TimestampError, parse_rfc3339_to_ms


def test_parse_rfc3339_zulu_to_integer_ms() -> None:
    assert parse_rfc3339_to_ms("1970-01-01T00:00:01.234Z") == 1234


def test_parse_rfc3339_offset_to_integer_ms() -> None:
    assert parse_rfc3339_to_ms("1970-01-01T09:00:00.000+09:00") == 0


def test_parse_rfc3339_truncates_sub_millisecond_fraction() -> None:
    assert parse_rfc3339_to_ms("1970-01-01T00:00:00.123999999Z") == 123


def test_parse_rfc3339_requires_timezone() -> None:
    with pytest.raises(TimestampError, match="timezone"):
        parse_rfc3339_to_ms("1970-01-01T00:00:00")

