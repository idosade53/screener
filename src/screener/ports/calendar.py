"""Trading-day arithmetic. All such arithmetic goes through this port, never through raw
date subtraction (architecture §8.1) — a weekend, a Thanksgiving and a July 3rd half-day are
all "the previous trading day" in different ways."""

from __future__ import annotations

from datetime import date, time
from typing import Protocol


class TradingCalendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...

    def previous_trading_day(self, d: date) -> date:
        """The last trading day strictly before ``d``."""
        ...

    def session_close(self, d: date) -> time:
        """Regular-session close in ET; 13:00 on half-days. ``d`` must be a trading day."""
        ...
