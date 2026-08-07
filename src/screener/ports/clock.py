"""The clock exists for one reason (ADR A4): M6 requires correct behaviour across a weekend,
a holiday and a half-day, tested with a frozen clock. A single ``datetime.now()`` call anywhere
outside a Clock implementation makes that test unwritable."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Always tz-aware UTC."""
        ...
