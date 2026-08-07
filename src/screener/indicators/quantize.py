"""The float -> Decimal edge. ``indicators/`` computes in float where pandas requires it;
values are quantised to 4 dp and handed back as Decimal at the boundary (architecture §4)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_QUANTUM = Decimal("0.0001")


def to_decimal_4dp(value: float) -> Decimal:
    """Quantise a float indicator/price value to a 4-dp Decimal (SC-3: agreement with
    TradingView to within rounding)."""
    return Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_UP)
