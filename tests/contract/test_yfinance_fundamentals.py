"""yfinance fundamentals fallback over a recorded ``Ticker`` stand-in — no network (PRD FR-2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from screener.adapters.fundamentals.yfinance_provider import YFinanceFundamentalsProvider
from screener.domain.errors import ProviderError

_INFO = {
    "longName": "Apple Inc.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "marketCap": 3200000000000,
    "currency": "USD",
    "exchange": "NMS",
    "trailingPE": 30.5,
    "forwardPE": 27.0,
    "priceToSalesTrailing12Months": 8.2,
    "trailingPegRatio": 2.1,
    "enterpriseToEbitda": 24.0,
    "priceToBook": 45.0,
    "revenueGrowth": 0.08,
    "earningsGrowth": 0.10,
    "grossMargins": 0.44,
    "operatingMargins": 0.30,
    "profitMargins": 0.25,
    "returnOnEquity": 1.5,
    "freeCashflow": 90000000000,
    "debtToEquity": 180.0,  # yfinance reports a percentage
    "currentRatio": 0.95,
    "recommendationKey": "buy",
    "numberOfAnalystOpinions": 34,
    "targetMeanPrice": 240.0,
}


class FakeTicker:
    def __init__(self, info: dict[str, Any], calendar: Any = None) -> None:
        self.info = info
        self.calendar = calendar


def _provider(ticker: FakeTicker) -> YFinanceFundamentalsProvider:
    return YFinanceFundamentalsProvider(ticker_fn=lambda _s: ticker)


def test_maps_info_to_snapshot() -> None:
    ticker = FakeTicker(_INFO, calendar={"Earnings Date": [date(2025, 1, 30), date(2025, 5, 1)]})
    prov = _provider(ticker)
    snap = prov.fetch_fundamentals("AAPL")
    assert snap.source == "yfinance"
    assert snap.pe_ttm == Decimal("30.5")
    assert snap.net_margin == Decimal("0.25")
    # debtToEquity 180.0% -> 1.8×
    assert snap.debt_to_equity == Decimal("1.8")
    assert snap.fcf_positive is True
    assert snap.mean_target == Decimal("240")
    assert snap.analyst_rating == "buy"
    assert snap.next_earnings_date == date(2025, 1, 30)


def test_profile_maps() -> None:
    profile = _provider(FakeTicker(_INFO)).fetch_profile("AAPL")
    assert profile.name == "Apple Inc."
    assert profile.market_cap == Decimal("3200000000000")


def test_empty_info_raises() -> None:
    with pytest.raises(ProviderError):
        _provider(FakeTicker({})).fetch_fundamentals("NOPE")


def test_validate_symbol() -> None:
    assert _provider(FakeTicker(_INFO)).validate_symbol("AAPL") is True
    assert _provider(FakeTicker({})).validate_symbol("NOPE") is False
