"""Contract tests for the yfinance adapter, exercised against recorded-shape frames — no network
(PRD testability). These frames reproduce the two column shapes yfinance actually returns: a
MultiIndex (ticker, field) for multiple symbols and a flat field index for a single symbol."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from screener.adapters.market_data.yfinance_provider import YFinanceProvider
from screener.domain.models import PriceMode

_FIELDS = ["Open", "High", "Low", "Close", "Volume"]
_Row = tuple[str, float, float, float, float, int]


def _multi_frame(data: dict[str, list[_Row]]) -> pd.DataFrame:
    """Build a MultiIndex-column frame like yfinance's multi-ticker download."""
    all_dates = sorted({row[0] for rows in data.values() for row in rows})
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in all_dates])
    cols = pd.MultiIndex.from_product([list(data), _FIELDS])
    frame = pd.DataFrame(index=index, columns=cols, dtype="float64")
    for sym, rows in data.items():
        for d, o, h, low, c, v in rows:
            ts = pd.Timestamp(d)
            frame.loc[ts, (sym, "Open")] = o
            frame.loc[ts, (sym, "High")] = h
            frame.loc[ts, (sym, "Low")] = low
            frame.loc[ts, (sym, "Close")] = c
            frame.loc[ts, (sym, "Volume")] = v
    return frame


def _flat_frame(rows: list[_Row]) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=index,
    )


def test_multi_symbol_normalises_to_per_symbol_bars() -> None:
    frame = _multi_frame(
        {
            "AAPL": [
                ("2026-08-05", 197.0, 199.0, 196.0, 198.0, 1000),
                ("2026-08-06", 198.0, 200.0, 197.5, 199.5, 1100),
            ],
            "KO": [
                ("2026-08-05", 60.0, 61.0, 59.5, 60.5, 500),
                ("2026-08-06", 60.5, 61.5, 60.0, 61.0, 550),
            ],
        }
    )
    provider = YFinanceProvider(download_fn=lambda s, a, b: frame)
    result = provider.fetch_daily_bars(["AAPL", "KO"], date(2026, 8, 1), date(2026, 8, 6))

    assert set(result.bars) == {"AAPL", "KO"}
    assert result.failures == {}
    assert [b.close for b in result.bars["AAPL"]] == [Decimal("198.0"), Decimal("199.5")]
    assert result.bars["KO"][0].volume == 500


def test_single_symbol_flat_frame() -> None:
    frame = _flat_frame(
        [
            ("2026-08-05", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-08-06", 100.5, 102.0, 100.0, 101.5, 12),
        ]
    )
    provider = YFinanceProvider(download_fn=lambda s, a, b: frame)
    result = provider.fetch_daily_bars(["AAPL"], date(2026, 8, 1), date(2026, 8, 6))
    assert [b.close for b in result.bars["AAPL"]] == [Decimal("100.5"), Decimal("101.5")]


def test_drops_bars_after_end_enforcing_completed_bars_only() -> None:
    # A bar dated after `end` (a still-forming session, §4.3) must be dropped.
    frame = _flat_frame(
        [
            ("2026-08-05", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-08-06", 100.5, 102.0, 100.0, 101.5, 12),  # end
            ("2026-08-07", 101.5, 103.0, 101.0, 102.5, 14),  # after end -> dropped
        ]
    )
    provider = YFinanceProvider(download_fn=lambda s, a, b: frame)
    result = provider.fetch_daily_bars(["AAPL"], date(2026, 8, 1), date(2026, 8, 6))
    assert [b.date for b in result.bars["AAPL"]] == [date(2026, 8, 5), date(2026, 8, 6)]


def test_nan_close_row_is_dropped() -> None:
    frame = _flat_frame(
        [
            ("2026-08-05", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-08-06", 100.5, 102.0, 100.0, 100.5, 12),
        ]
    )
    frame.loc[pd.Timestamp("2026-08-06"), "Close"] = np.nan
    provider = YFinanceProvider(download_fn=lambda s, a, b: frame)
    result = provider.fetch_daily_bars(["AAPL"], date(2026, 8, 1), date(2026, 8, 6))
    assert [b.date for b in result.bars["AAPL"]] == [date(2026, 8, 5)]


def test_missing_symbol_becomes_a_failure_not_an_exception() -> None:
    frame = _multi_frame(
        {"AAPL": [("2026-08-06", 198.0, 200.0, 197.5, 199.5, 1100)]}
    )
    provider = YFinanceProvider(download_fn=lambda s, a, b: frame)
    result = provider.fetch_daily_bars(["AAPL", "DELISTED"], date(2026, 8, 1), date(2026, 8, 6))
    assert "AAPL" in result.bars
    assert "DELISTED" in result.failures


def test_empty_batch_retried_then_reported_as_failures() -> None:
    calls = {"n": 0}

    def flaky(_s: list[str], _a: date, _b: date) -> pd.DataFrame:
        calls["n"] += 1
        return pd.DataFrame()  # always empty

    provider = YFinanceProvider(
        download_fn=flaky, retries=3, sleep_fn=lambda _: None
    )
    result = provider.fetch_daily_bars(["AAPL"], date(2026, 8, 1), date(2026, 8, 6))
    assert calls["n"] == 3  # retried
    assert result.bars == {}
    assert set(result.failures) == {"AAPL"}


def test_download_exception_is_retried_and_contained() -> None:
    calls = {"n": 0}

    def boom(_s: list[str], _a: date, _b: date) -> pd.DataFrame:
        calls["n"] += 1
        raise RuntimeError("network down")

    provider = YFinanceProvider(download_fn=boom, retries=2, sleep_fn=lambda _: None)
    result = provider.fetch_daily_bars(["AAPL"], date(2026, 8, 1), date(2026, 8, 6))
    assert calls["n"] == 2
    assert set(result.failures) == {"AAPL"}


def test_empty_symbol_list_short_circuits() -> None:
    provider = YFinanceProvider(download_fn=lambda s, a, b: pd.DataFrame())
    result = provider.fetch_daily_bars([], date(2026, 8, 1), date(2026, 8, 6))
    assert result.bars == {} and result.failures == {}


def _chunk_frame(symbols: list[str]) -> pd.DataFrame:
    return _multi_frame(
        {s: [("2026-08-06", 100.0, 101.0, 99.0, 100.5, 10)] for s in symbols}
    )


def test_universe_is_downloaded_in_batches_with_pauses_between_them() -> None:
    seen_chunks: list[list[str]] = []
    pauses: list[float] = []

    def download(symbols: list[str], _a: date, _b: date) -> pd.DataFrame:
        seen_chunks.append(list(symbols))
        return _chunk_frame(symbols)

    provider = YFinanceProvider(
        download_fn=download, batch_size=2, batch_pause=1.5, sleep_fn=pauses.append
    )
    result = provider.fetch_daily_bars(
        ["AAPL", "KO", "MSFT", "META", "NVDA"], date(2026, 8, 1), date(2026, 8, 6)
    )

    # 5 symbols in batches of 2 -> three requests, each seeing only its own slice.
    assert seen_chunks == [["AAPL", "KO"], ["MSFT", "META"], ["NVDA"]]
    assert set(result.bars) == {"AAPL", "KO", "MSFT", "META", "NVDA"}
    # A pause between each pair of batches (two gaps), never before the first.
    assert pauses == [1.5, 1.5]


def test_one_throttled_batch_fails_only_its_own_symbols() -> None:
    def download(symbols: list[str], _a: date, _b: date) -> pd.DataFrame:
        # The second batch (MSFT/META) is throttled: Yahoo returns an empty frame.
        if "MSFT" in symbols:
            return pd.DataFrame()
        return _chunk_frame(symbols)

    provider = YFinanceProvider(
        download_fn=download, batch_size=2, retries=2, sleep_fn=lambda _: None
    )
    result = provider.fetch_daily_bars(
        ["AAPL", "KO", "MSFT", "META"], date(2026, 8, 1), date(2026, 8, 6)
    )

    assert set(result.bars) == {"AAPL", "KO"}  # healthy batch still delivered
    assert set(result.failures) == {"MSFT", "META"}  # throttled batch only


class _FakeFastInfo:
    def __init__(self, last_price: float | None) -> None:
        self._p = last_price

    def get(self, key: str) -> float | None:
        return self._p if key == "last_price" else None


class _FakeTicker:
    def __init__(self, last_price: float | None, has_history: bool = True) -> None:
        self.fast_info = _FakeFastInfo(last_price)
        self._has_history = has_history

    def history(self, *args: object, **kwargs: object) -> pd.DataFrame:
        if not self._has_history:
            return pd.DataFrame()
        return pd.DataFrame({"Close": [1.0]})


def test_fetch_quotes_returns_live_price() -> None:
    provider = YFinanceProvider(ticker_fn=lambda s: _FakeTicker(198.4))
    result = provider.fetch_quotes(["AAPL"], PriceMode.REGULAR)
    assert result.quotes["AAPL"].price == Decimal("198.4")
    assert result.quotes["AAPL"].is_stale is False


def test_fetch_quotes_absent_price_is_omitted_for_fallback() -> None:
    provider = YFinanceProvider(ticker_fn=lambda s: _FakeTicker(None))
    result = provider.fetch_quotes(["AAPL"], PriceMode.PREMARKET)
    assert "AAPL" not in result.quotes  # caller falls back to previous close (Q2)


def test_validate_symbol() -> None:
    provider = YFinanceProvider(ticker_fn=lambda s: _FakeTicker(1.0, has_history=s == "AAPL"))
    assert provider.validate_symbol("AAPL") is True
    assert provider.validate_symbol("NOPE") is False
