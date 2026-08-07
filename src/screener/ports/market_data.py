"""The riskiest port (driver D2), so the most tightly specified. Provider access sits behind
this interface so it can be swapped for a paid feed without touching indicators or alerting.

Contract obligations every implementation must satisfy (architecture §5.1):
- Never raises for a single-symbol failure. Individual failures are data in the result.
- Never returns a partial or provisional bar for a session that has not closed. If the provider
  offers today's incomplete bar, the adapter drops it. This enforces PRD §4.3.
- Returns unadjusted OHLC. Adjustment is the caller's concern.
- Retries (3×, exponential backoff, jitter) live inside the adapter. The core never retries.
- A 200-symbol fetch cannot exceed the 60 s NFR budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

from screener.domain.models import Bar, PriceMode


@dataclass(frozen=True)
class BarFetchResult:
    """Batched daily-bar fetch. Partial success is normal: bars for the symbols that
    succeeded, plus a per-symbol reason for those that failed."""

    bars: dict[str, list[Bar]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)  # symbol -> reason


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: Decimal
    is_stale: bool  # True when the provider had no live trade and returned a fallback close


@dataclass(frozen=True)
class QuoteFetchResult:
    quotes: dict[str, Quote] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)  # symbol -> reason


class MarketDataProvider(Protocol):
    def fetch_daily_bars(
        self, symbols: list[str], start: date, end: date
    ) -> BarFetchResult:
        """Batched (FR-2). MUST NOT loop per symbol. Partial success is normal: returns
        bars-by-symbol AND failures-by-symbol."""
        ...

    def fetch_quotes(self, symbols: list[str], mode: PriceMode) -> QuoteFetchResult:
        """PREMARKET may return no trade for a symbol -> the caller falls back to the
        previous close (Q2). The provider does NOT do the fallback; it reports absence
        honestly (a missing symbol in ``quotes``, or a reason in ``failures``)."""
        ...

    def validate_symbol(self, symbol: str) -> bool:
        """Used by /add before a symbol enters the universe (FR-1)."""
        ...
