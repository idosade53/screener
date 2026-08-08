"""Scorecard engine audit (F2, SC-3): five hand-checked fixtures — mega-cap, value,
growth-unprofitable, high-debt, insufficient-data — with the flag on every row and the headline
tally computed by hand. Pure function, no network, no fixtures files."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal

from screener.domain.models import Flag, FundamentalsSnapshot, ScoreCategory
from screener.fundamentals.scorecard import score
from screener.fundamentals.thresholds import ScorecardThresholds

_TODAY = date(2026, 8, 10)  # a Monday
_T = ScorecardThresholds.default()

# Base snapshot: everything absent. Fixtures override only the fields they exercise, so an
# untouched category deterministically lands on NA.
_BASE = FundamentalsSnapshot(
    symbol="TST",
    fetched_at=datetime(2026, 8, 10, tzinfo=UTC),
    source="fixture",
    next_earnings_date=None,
    pe_ttm=None,
    pe_fwd=None,
    price_to_sales=None,
    peg=None,
    ev_ebitda=None,
    price_to_book=None,
    revenue_yoy=None,
    eps_yoy=None,
    revenue_cagr_3y=None,
    gross_margin=None,
    operating_margin=None,
    net_margin=None,
    roe=None,
    fcf_positive=None,
    debt_to_equity=None,
    current_ratio=None,
    net_debt_to_ebitda=None,
    interest_coverage=None,
    analyst_rating=None,
    num_analysts=None,
    mean_target=None,
    last_earnings_surprise_pct=None,
)


def _snap(**overrides: object) -> FundamentalsSnapshot:
    return dataclasses.replace(_BASE, **overrides)  # type: ignore[arg-type]


def _flags(snapshot: FundamentalsSnapshot, *, price: Decimal | None) -> dict[ScoreCategory, Flag]:
    card = score(snapshot, _T, today=_TODAY, price=price)
    return {line.category: line.flag for line in card.lines}


def test_mega_cap_all_green() -> None:
    snap = _snap(
        pe_ttm=Decimal("18"),
        peg=Decimal("1.2"),
        revenue_yoy=Decimal("0.20"),
        eps_yoy=Decimal("0.25"),
        net_margin=Decimal("0.25"),
        fcf_positive=True,
        debt_to_equity=Decimal("0.3"),
        current_ratio=Decimal("2.0"),
        analyst_rating="Buy",
        num_analysts=30,
        mean_target=Decimal("120"),
        next_earnings_date=date(2026, 9, 15),
    )
    flags = _flags(snap, price=Decimal("100"))
    assert flags == {
        ScoreCategory.VALUATION: Flag.GREEN,
        ScoreCategory.GROWTH: Flag.GREEN,
        ScoreCategory.PROFITABILITY: Flag.GREEN,
        ScoreCategory.BALANCE_SHEET: Flag.GREEN,
        ScoreCategory.ANALYST: Flag.GREEN,
        ScoreCategory.EARNINGS_TIMING: Flag.GREEN,
    }
    assert score(snap, _T, today=_TODAY, price=Decimal("100")).tally == "6🟢"


def test_value_cheap_but_ordinary() -> None:
    # Cheap on earnings (green valuation) but modest everywhere else.
    snap = _snap(
        pe_ttm=Decimal("12"),
        peg=None,  # missing PEG must not veto a clearly-cheap name
        revenue_yoy=Decimal("0.05"),
        eps_yoy=Decimal("0.04"),
        net_margin=Decimal("0.08"),
        fcf_positive=True,
        debt_to_equity=Decimal("0.6"),
        current_ratio=Decimal("1.6"),
        analyst_rating="Hold",
        num_analysts=12,
        mean_target=Decimal("105"),
        next_earnings_date=date(2026, 8, 17),  # ~5 trading days out
    )
    flags = _flags(snap, price=Decimal("100"))
    assert flags == {
        ScoreCategory.VALUATION: Flag.GREEN,
        ScoreCategory.GROWTH: Flag.YELLOW,
        ScoreCategory.PROFITABILITY: Flag.YELLOW,
        ScoreCategory.BALANCE_SHEET: Flag.YELLOW,
        ScoreCategory.ANALYST: Flag.YELLOW,
        ScoreCategory.EARNINGS_TIMING: Flag.YELLOW,
    }
    assert score(snap, _T, today=_TODAY, price=Decimal("100")).tally == "1🟢 5🟡"


def test_growth_unprofitable() -> None:
    snap = _snap(
        pe_ttm=Decimal("-50"),  # negative earnings -> red valuation
        revenue_yoy=Decimal("0.40"),  # green growth
        eps_yoy=Decimal("-0.10"),
        net_margin=Decimal("-0.15"),  # red profitability
        fcf_positive=False,
        debt_to_equity=Decimal("1.0"),
        current_ratio=Decimal("1.2"),  # moderate -> yellow
        analyst_rating="Strong Buy",
        num_analysts=20,
        mean_target=Decimal("150"),  # +50% upside -> green analyst
        next_earnings_date=date(2026, 9, 15),  # green earnings timing
    )
    flags = _flags(snap, price=Decimal("100"))
    assert flags == {
        ScoreCategory.VALUATION: Flag.RED,
        ScoreCategory.GROWTH: Flag.GREEN,
        ScoreCategory.PROFITABILITY: Flag.RED,
        ScoreCategory.BALANCE_SHEET: Flag.YELLOW,
        ScoreCategory.ANALYST: Flag.GREEN,
        ScoreCategory.EARNINGS_TIMING: Flag.GREEN,
    }
    assert score(snap, _T, today=_TODAY, price=Decimal("100")).tally == "3🟢 1🟡 2🔴"


def test_high_debt() -> None:
    # Analyst + earnings absent -> NA (omitted from the tally); the balance sheet is the story.
    snap = _snap(
        pe_ttm=Decimal("25"),  # yellow
        revenue_yoy=Decimal("0.10"),  # yellow
        net_margin=Decimal("0.05"),
        fcf_positive=True,  # thin -> yellow
        debt_to_equity=Decimal("3.0"),  # red
        current_ratio=Decimal("0.8"),
    )
    flags = _flags(snap, price=None)
    assert flags == {
        ScoreCategory.VALUATION: Flag.YELLOW,
        ScoreCategory.GROWTH: Flag.YELLOW,
        ScoreCategory.PROFITABILITY: Flag.YELLOW,
        ScoreCategory.BALANCE_SHEET: Flag.RED,
        ScoreCategory.ANALYST: Flag.NA,
        ScoreCategory.EARNINGS_TIMING: Flag.NA,
    }
    assert score(snap, _T, today=_TODAY, price=None).tally == "3🟡 1🔴"


def test_insufficient_data_all_na() -> None:
    snap = _snap()  # everything absent
    flags = _flags(snap, price=None)
    assert set(flags.values()) == {Flag.NA}
    # No non-NA lines -> an empty tally.
    assert score(snap, _T, today=_TODAY, price=None).tally == ""
