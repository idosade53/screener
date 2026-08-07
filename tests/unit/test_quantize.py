from __future__ import annotations

from decimal import Decimal

from screener.indicators.quantize import to_decimal_4dp


def test_quantize_to_four_places() -> None:
    assert to_decimal_4dp(11.333333333) == Decimal("11.3333")


def test_quantize_half_up() -> None:
    assert to_decimal_4dp(1.00005) == Decimal("1.0001")


def test_quantize_preserves_trailing_zeros() -> None:
    assert to_decimal_4dp(50.0) == Decimal("50.0000")
