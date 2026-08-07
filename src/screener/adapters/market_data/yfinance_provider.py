"""yfinance implementation of ``MarketDataProvider``. The riskiest, most likely-to-be-replaced
component (driver D2), so everything yfinance-shaped is normalised here and nothing leaks past
this edge (architecture §5.1).

Network calls go through two injectable seams — ``download_fn`` (bars) and ``ticker_fn``
(quotes/validation) — so contract tests replay recorded frames with no network (PRD testability).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

import pandas as pd

from screener.domain.models import Bar, PriceMode
from screener.ports.market_data import (
    BarFetchResult,
    Quote,
    QuoteFetchResult,
)


class _TickerLike(Protocol):
    fast_info: Any

    def history(self, *args: Any, **kwargs: Any) -> pd.DataFrame: ...


def _default_download(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    # yfinance `end` is exclusive; add a day to include the `end` session.
    return yf.download(
        list(symbols),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,  # unadjusted OHLC for ATR correctness (FR-2)
        group_by="ticker",
        progress=False,
        threads=True,
    )


def _default_ticker(symbol: str) -> _TickerLike:
    import yfinance as yf

    return cast(_TickerLike, yf.Ticker(symbol))


def _quantize(value: float) -> Decimal:
    return Decimal(str(round(float(value), 4)))


class YFinanceProvider:
    def __init__(
        self,
        download_fn: Callable[[list[str], date, date], pd.DataFrame] = _default_download,
        ticker_fn: Callable[[str], _TickerLike] = _default_ticker,
        retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._download_fn = download_fn
        self._ticker_fn = ticker_fn
        self._retries = retries
        self._backoff_base = backoff_base
        self._sleep = sleep_fn

    # ---------------------------------------------------------------- daily bars
    def fetch_daily_bars(
        self, symbols: list[str], start: date, end: date
    ) -> BarFetchResult:
        if not symbols:
            return BarFetchResult()

        raw = self._with_retries(lambda: self._download_fn(symbols, start, end))
        if raw is None:
            # Whole batch failed after retries — every symbol is a failure, not an exception.
            return BarFetchResult(failures={s: "batch download failed" for s in symbols})

        per_symbol = self._split_by_symbol(raw, symbols)
        bars: dict[str, list[Bar]] = {}
        failures: dict[str, str] = {}
        for sym in symbols:
            df = per_symbol.get(sym)
            if df is None or df.empty:
                failures[sym] = "no data returned"
                continue
            sym_bars = self._to_bars(df, start, end)
            if sym_bars:
                bars[sym] = sym_bars
            else:
                failures[sym] = "no complete bars in range"
        return BarFetchResult(bars=bars, failures=failures)

    def _split_by_symbol(
        self, raw: pd.DataFrame, symbols: list[str]
    ) -> dict[str, pd.DataFrame]:
        """yfinance changes its column shape with the number of tickers requested. With
        multiple tickers and group_by='ticker' the columns are a MultiIndex (ticker, field);
        with a single ticker there is no ticker level. Normalise both to per-symbol frames."""
        out: dict[str, pd.DataFrame] = {}
        if isinstance(raw.columns, pd.MultiIndex):
            top = set(raw.columns.get_level_values(0))
            for sym in symbols:
                if sym in top:
                    out[sym] = raw[sym]
        else:
            # Single-symbol frame: fields are the columns, and there is exactly one symbol.
            if len(symbols) == 1:
                out[symbols[0]] = raw
        return out

    def _to_bars(self, df: pd.DataFrame, start: date, end: date) -> list[Bar]:
        bars: list[Bar] = []
        for idx, row in df.iterrows():
            bar_date = pd.Timestamp(idx).date()
            # §4.3: only completed bars within the requested window. The caller sets `end`
            # to the last completed session, so filtering to <= end drops today's forming bar.
            if bar_date < start or bar_date > end:
                continue
            close = row.get("Close")
            if close is None or (isinstance(close, float) and math.isnan(close)):
                continue  # yfinance silently emits NaN closes; drop them
            try:
                bars.append(
                    Bar(
                        date=bar_date,
                        open=_quantize(row["Open"]),
                        high=_quantize(row["High"]),
                        low=_quantize(row["Low"]),
                        close=_quantize(row["Close"]),
                        volume=int(row["Volume"]) if not math.isnan(row["Volume"]) else 0,
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        bars.sort(key=lambda b: b.date)
        return bars

    # -------------------------------------------------------------------- quotes
    def fetch_quotes(self, symbols: list[str], mode: PriceMode) -> QuoteFetchResult:
        quotes: dict[str, Quote] = {}
        failures: dict[str, str] = {}
        for sym in symbols:
            try:
                price = self._last_price(sym, mode)
            except Exception as exc:  # noqa: BLE001 — provider errors become data, not control
                failures[sym] = str(exc)
                continue
            if price is None:
                # Absence is honest: caller falls back to previous close (Q2). Not a failure
                # in REGULAR/CLOSE, but for PREMARKET it just means no extended-hours trade.
                continue
            quotes[sym] = Quote(symbol=sym, price=_quantize(price), is_stale=False)
        return QuoteFetchResult(quotes=quotes, failures=failures)

    def _last_price(self, symbol: str, mode: PriceMode) -> float | None:
        ticker = self._ticker_fn(symbol)
        info = getattr(ticker, "fast_info", None)
        if info is None:
            return None
        # fast_info exposes last_price for regular hours; extended-hours coverage on the free
        # feed is thin (risk R2), so PREMARKET may simply return None and the caller falls back.
        last = None
        try:
            last = info.get("last_price") if hasattr(info, "get") else info["last_price"]
        except (KeyError, TypeError):
            last = getattr(info, "last_price", None)
        if last is None or (isinstance(last, float) and math.isnan(last)):
            return None
        return float(last)

    # ---------------------------------------------------------------- validation
    def validate_symbol(self, symbol: str) -> bool:
        try:
            hist = self._ticker_fn(symbol).history(period="5d")
        except Exception:  # noqa: BLE001
            return False
        return isinstance(hist, pd.DataFrame) and not hist.empty

    # ----------------------------------------------------------------- internals
    def _with_retries(self, call: Callable[[], pd.DataFrame]) -> pd.DataFrame | None:
        for attempt in range(self._retries):
            try:
                result = call()
            except Exception:  # noqa: BLE001 — retry transient provider failures internally
                result = None
            if result is not None and not (
                isinstance(result, pd.DataFrame) and result.empty
            ):
                return result
            if attempt < self._retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
        return None
