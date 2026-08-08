"""FMP fundamentals adapter over recorded JSON frames — no network (PRD FR-2, SC-4). The injected
``http_get_fn`` maps a request URL to a canned payload; a section can be made to raise or come back
empty to prove graceful degradation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from screener.adapters.fundamentals.fmp_provider import FmpFundamentalsProvider
from screener.domain.errors import ProviderError

_PROFILE = [
    {
        "symbol": "AAPL",
        "companyName": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "mktCap": 3200000000000,
        "currency": "USD",
        "exchangeShortName": "NASDAQ",
    }
]
_RATIOS = [
    {
        "peRatioTTM": 30.5,
        "priceEarningsToGrowthRatioTTM": 2.1,
        "priceToSalesRatioTTM": 8.2,
        "priceToBookRatioTTM": 45.0,
        "grossProfitMarginTTM": 0.44,
        "operatingProfitMarginTTM": 0.30,
        "netProfitMarginTTM": 0.25,
        "returnOnEquityTTM": 1.5,
        "debtEquityRatioTTM": 1.8,
        "currentRatioTTM": 0.95,
        "interestCoverageTTM": 28.0,
    }
]
_METRICS = [
    {
        "enterpriseValueOverEBITDATTM": 24.0,
        "netDebtToEBITDATTM": 0.5,
        "freeCashFlowPerShareTTM": 6.4,
        "forwardPETTM": 27.0,
    }
]
_INCOME = [
    {"date": "2024-09-30", "revenue": 391000, "eps": 6.10},
    {"date": "2023-09-30", "revenue": 383000, "eps": 6.00},
]
_TARGET = [{"targetConsensus": 240.0, "numberOfAnalysts": 34, "consensus": "Buy"}]
_EARNINGS = [
    {"date": "2025-01-30"},
    {"date": "2025-05-01"},
    {"date": "2024-11-01"},
]


class FakeHttp:
    def __init__(self, frames: dict[str, Any], *, raise_on: set[str] | None = None) -> None:
        self.frames = frames
        self.raise_on = raise_on or set()
        self.calls: list[str] = []

    def __call__(self, url: str, params: dict[str, str], timeout: float) -> Any:
        key = next((k for k in {**self.frames, **{r: None for r in self.raise_on}} if k in url), "")
        self.calls.append(key)
        if key in self.raise_on:
            raise RuntimeError("boom")
        return self.frames.get(key, [])


def _frames() -> dict[str, Any]:
    return {
        "profile/": _PROFILE,
        "ratios-ttm/": _RATIOS,
        "key-metrics-ttm/": _METRICS,
        "income-statement/": _INCOME,
        "price-target-consensus/": _TARGET,
        "historical/earning_calendar/": _EARNINGS,
    }


def _provider(http: FakeHttp) -> FmpFundamentalsProvider:
    return FmpFundamentalsProvider(
        "KEY",
        http_get_fn=http,
        today_fn=lambda: date(2025, 1, 1),
        retries=1,
        sleep_fn=lambda _: None,
    )


def test_fetch_profile_maps_fields() -> None:
    prov = _provider(FakeHttp(_frames()))
    profile = prov.fetch_profile("AAPL")
    assert profile.name == "Apple Inc."
    assert profile.sector == "Technology"
    assert profile.market_cap == Decimal("3200000000000")
    assert profile.exchange == "NASDAQ"


def test_fetch_fundamentals_normalises_to_decimal() -> None:
    prov = _provider(FakeHttp(_frames()))
    snap = prov.fetch_fundamentals("AAPL")
    assert snap.source == "fmp"
    assert snap.pe_ttm == Decimal("30.5")
    assert isinstance(snap.net_margin, Decimal)
    assert snap.net_margin == Decimal("0.25")
    assert snap.ev_ebitda == Decimal("24")
    assert snap.net_debt_to_ebitda == Decimal("0.5")
    assert snap.fcf_positive is True
    assert snap.mean_target == Decimal("240")
    assert snap.num_analysts == 34
    assert snap.analyst_rating == "Buy"
    # YoY from the two annual statements: (391000-383000)/383000 ≈ 0.0209
    assert snap.revenue_yoy == Decimal("0.0209")
    # Next earnings: soonest date on/after 2025-01-01.
    assert snap.next_earnings_date == date(2025, 1, 30)


def test_absent_sections_become_none_not_error() -> None:
    # Only the profile responds; every other endpoint returns empty.
    prov = _provider(FakeHttp({"profile/": _PROFILE}))
    snap = prov.fetch_fundamentals("AAPL")
    assert snap.pe_ttm is None
    assert snap.net_margin is None
    assert snap.mean_target is None
    assert snap.next_earnings_date is None


def test_partial_outage_degrades_ratios_to_none() -> None:
    # Ratios endpoint raises; the rest still map (graceful degradation, SC-4).
    http = FakeHttp(_frames(), raise_on={"ratios-ttm/"})
    snap = _provider(http).fetch_fundamentals("AAPL")
    assert snap.pe_ttm is None  # came from the failed ratios call
    assert snap.ev_ebitda == Decimal("24")  # key-metrics still worked
    assert snap.mean_target == Decimal("240")


def test_total_outage_raises_provider_error() -> None:
    # No profile at all -> signal a fallback to the assembler.
    http = FakeHttp({}, raise_on={"profile/"})
    with pytest.raises(ProviderError):
        _provider(http).fetch_fundamentals("AAPL")


def test_validate_symbol() -> None:
    assert _provider(FakeHttp(_frames())).validate_symbol("AAPL") is True
    assert _provider(FakeHttp({})).validate_symbol("NOPE") is False
