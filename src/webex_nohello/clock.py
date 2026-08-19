"""Injectable clock, so token expiry logic is testable without waiting (Article III.7)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

type Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)
