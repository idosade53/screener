"""Scorecard thresholds as a configurable value object (PRD §4.2). Thresholds are *global* — the
same numbers for every symbol; sector-relative tuning is deferred (PRD §13). Kept out of the
scoring logic so the cut-offs can be tuned (or, later, injected from config) without touching
``scorecard.py``. All values are ``Decimal`` to match the project numeric policy (ADR A11)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ScorecardThresholds:
    # Valuation (P/E, PEG)
    pe_green: Decimal = Decimal("20")  # P/E below this is GREEN (with PEG)
    pe_red: Decimal = Decimal("40")  # P/E above this (or negative earnings) is RED
    peg_green: Decimal = Decimal("1.5")

    # Growth (revenue YoY, as a fraction: 0.15 == +15%)
    rev_yoy_green: Decimal = Decimal("0.15")

    # Profitability (net margin, as a fraction)
    net_margin_green: Decimal = Decimal("0.10")

    # Balance sheet (debt/equity, current ratio)
    de_green: Decimal = Decimal("0.5")
    de_red: Decimal = Decimal("2")
    current_ratio_green: Decimal = Decimal("1.5")
    current_ratio_red: Decimal = Decimal("1")

    # Analyst view (mean-target upside vs current price, as a fraction)
    analyst_upside_green: Decimal = Decimal("0.15")

    # Earnings timing (business days until the next release)
    earnings_green_days: int = 10  # strictly more than this is GREEN
    earnings_red_days: int = 2  # at or below this is RED ("don't buy into the print")

    @classmethod
    def default(cls) -> ScorecardThresholds:
        return cls()
