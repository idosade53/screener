"""The criterion, defined as a composable predicate so the next indicator drops in without
rework (PRD §10). Phase 1 registers exactly one: MA150 proximity."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from screener.domain.models import Indicators
from screener.indicators.quantize import to_decimal_4dp


@dataclass(frozen=True)
class CriterionResult:
    name: str
    passed: bool
    distance_atr: Decimal | None  # signed (P - SMA150) / ATR14; None if undefined


class Criterion(Protocol):
    name: str

    def evaluate(self, indicators: Indicators, price: Decimal) -> CriterionResult: ...


class MA150ProximityCriterion:
    """In range when ``abs((P - SMA150) / ATR14) <= band_atr_mult`` (PRD §4.1). The band is
    symmetric; the sign of the distance is preserved so the alert can show which side."""

    name = "ma150_proximity"

    def __init__(self, band_atr_mult: Decimal) -> None:
        self._band = band_atr_mult

    def evaluate(self, indicators: Indicators, price: Decimal) -> CriterionResult:
        if indicators.atr14 == 0:
            # Degenerate ATR (a flat symbol): distance is undefined, so it cannot qualify.
            return CriterionResult(name=self.name, passed=False, distance_atr=None)
        # Test the band on full precision, but store the distance quantised to the 4-dp
        # boundary policy (architecture §4).
        distance = (price - indicators.sma150) / indicators.atr14
        passed = abs(distance) <= self._band
        return CriterionResult(
            name=self.name, passed=passed, distance_atr=to_decimal_4dp(float(distance))
        )
