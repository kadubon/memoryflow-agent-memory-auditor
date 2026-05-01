"""RFC3339 timestamp parsing for deterministic millisecond conversion."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_RFC3339_PATTERN = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?(?P<tz>Z|[+-]\d{2}:\d{2})$"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TimestampError(ValueError):
    """Raised when a timestamp is not valid RFC3339 with timezone."""


def parse_rfc3339_to_ms(value: object) -> int:
    """Parse an RFC3339 timestamp to integer Unix milliseconds.

    Fractions beyond milliseconds are deterministically truncated toward zero.
    The function requires an explicit timezone designator.
    """

    if not isinstance(value, str):
        raise TimestampError("timestamp must be a string")
    match = _RFC3339_PATTERN.match(value)
    if match is None:
        raise TimestampError("timestamp must be RFC3339 with an explicit timezone")

    fraction = match.group("fraction") or ""
    microsecond_fraction = fraction[:6].ljust(6, "0")
    tz = "+00:00" if match.group("tz") == "Z" else match.group("tz")
    normalized = f"{match.group('base')}.{microsecond_fraction}{tz}"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TimestampError("timestamp is not a valid RFC3339 datetime") from exc
    if dt.tzinfo is None:
        raise TimestampError("timestamp must include timezone information")
    dt_utc = dt.astimezone(UTC)
    delta = dt_utc - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000
