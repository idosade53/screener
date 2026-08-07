"""System clock — always returns tz-aware UTC (architecture §8.1)."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A fixed clock for tests (M6). Kept in the adapter layer so nothing in the core needs a
    test double at import time."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FrozenClock requires a tz-aware datetime")
        self._moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment
