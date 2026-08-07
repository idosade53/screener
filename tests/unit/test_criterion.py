from __future__ import annotations

from datetime import date
from decimal import Decimal

from screener.domain.models import Indicators
from screener.screener.criterion import MA150ProximityCriterion


def _ind(sma: str, atr: str) -> Indicators:
    return Indicators(sma150=Decimal(sma), atr14=Decimal(atr), asof=date(2026, 8, 6))


def test_in_range_within_band() -> None:
    crit = MA150ProximityCriterion(Decimal("1.5"))
    # price 101, sma 100, atr 2 -> distance +0.5 -> in range
    res = crit.evaluate(_ind("100", "2"), Decimal("101"))
    assert res.passed is True
    assert res.distance_atr == Decimal("0.5")


def test_out_of_range_above_band() -> None:
    crit = MA150ProximityCriterion(Decimal("1.5"))
    # price 104, sma 100, atr 2 -> distance +2.0 -> out
    res = crit.evaluate(_ind("100", "2"), Decimal("104"))
    assert res.passed is False
    assert res.distance_atr == Decimal("2")


def test_symmetric_below_ma() -> None:
    crit = MA150ProximityCriterion(Decimal("1.5"))
    # price 97, sma 100, atr 2 -> distance -1.5 -> exactly on the boundary, inclusive
    res = crit.evaluate(_ind("100", "2"), Decimal("97"))
    assert res.passed is True
    assert res.distance_atr == Decimal("-1.5")


def test_zero_atr_is_not_in_range() -> None:
    crit = MA150ProximityCriterion(Decimal("1.5"))
    res = crit.evaluate(_ind("100", "0"), Decimal("100"))
    assert res.passed is False
    assert res.distance_atr is None
