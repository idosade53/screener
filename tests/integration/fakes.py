"""In-memory fakes for integration tests. The repository fake reuses the SQLite adapter (an
in-memory DB) so it already passes the repository contract. The provider fake serves recorded
bars/quotes with no network."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal

from screener.domain.models import Bar, PriceMode
from screener.ports.market_data import (
    BarFetchResult,
    Quote,
    QuoteFetchResult,
)


class FakeProvider:
    """Serves pre-seeded bars and quotes. Failures and missing quotes are configurable so the
    failure taxonomy (§8.3) can be exercised."""

    def __init__(self) -> None:
        self.bars: dict[str, list[Bar]] = {}
        self.quotes: dict[str, Decimal] = {}
        self.bar_failures: dict[str, str] = {}
        self.quote_failures: dict[str, str] = {}

    def seed_bars(self, symbol: str, bars: list[Bar]) -> None:
        self.bars[symbol] = bars

    def fetch_daily_bars(
        self, symbols: list[str], start: date, end: date
    ) -> BarFetchResult:
        out: dict[str, list[Bar]] = {}
        failures: dict[str, str] = {}
        for sym in symbols:
            if sym in self.bar_failures:
                failures[sym] = self.bar_failures[sym]
                continue
            if sym not in self.bars:
                failures[sym] = "no data"
                continue
            out[sym] = [b for b in self.bars[sym] if start <= b.date <= end]
        return BarFetchResult(bars=out, failures=failures)

    def fetch_quotes(self, symbols: list[str], mode: PriceMode) -> QuoteFetchResult:
        quotes: dict[str, Quote] = {}
        failures: dict[str, str] = {}
        for sym in symbols:
            if sym in self.quote_failures:
                failures[sym] = self.quote_failures[sym]
                continue
            if sym in self.quotes:
                quotes[sym] = Quote(symbol=sym, price=self.quotes[sym], is_stale=False)
        return QuoteFetchResult(quotes=quotes, failures=failures)

    def validate_symbol(self, symbol: str) -> bool:
        return symbol in self.bars


class FakeCalendar:
    """Weekday calendar: every Mon–Fri is a trading day, 16:00 close. Enough for pipeline
    integration tests; the real XNYS holiday matrix is tested separately (M6)."""

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5

    def previous_trading_day(self, d: date) -> date:
        from datetime import timedelta

        cur = d - timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
        return cur

    def session_close(self, d: date) -> time:
        return time(16, 0)


class FrozenClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class CollectingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> object:
        from screener.domain.models import DeliveryStatus

        self.messages.append(message)
        return DeliveryStatus.SENT


def make_bars(
    start: date, closes: Sequence[str], *, high_pad: str = "1", low_pad: str = "1"
) -> list[Bar]:
    """Build a run of daily bars with a fixed ±pad range around each close, one calendar day
    apart (weekends included — the FakeCalendar only gates scan scheduling, not bar dates)."""
    from datetime import timedelta

    bars: list[Bar] = []
    d = start
    for c in closes:
        close = Decimal(c)
        bars.append(
            Bar(
                date=d,
                open=close,
                high=close + Decimal(high_pad),
                low=close - Decimal(low_pad),
                close=close,
                volume=1000,
            )
        )
        d += timedelta(days=1)
    return bars
