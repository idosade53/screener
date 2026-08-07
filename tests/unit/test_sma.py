"""SMA unit tests. Hand-computed expectations — the SMA of the closing series over ``period``
completed bars, verifiable by arithmetic."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from screener.indicators.sma import sma


def test_sma_hand_computed_period_3() -> None:
    closes = pd.Series([10.0, 11.0, 12.0, 11.0, 13.0, 14.0])
    result = sma(closes, 3)

    # First period-1 entries are NaN.
    assert math.isnan(result.iloc[0])
    assert math.isnan(result.iloc[1])
    # Hand-computed rolling means.
    assert result.iloc[2] == pytest.approx((10 + 11 + 12) / 3)  # 11.0
    assert result.iloc[3] == pytest.approx((11 + 12 + 11) / 3)  # 11.3333
    assert result.iloc[4] == pytest.approx((12 + 11 + 13) / 3)  # 12.0
    assert result.iloc[5] == pytest.approx((11 + 13 + 14) / 3)  # 12.6667


def test_sma_constant_series_equals_constant() -> None:
    closes = pd.Series([50.0] * 200)
    result = sma(closes, 150)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_sma_requires_full_window() -> None:
    closes = pd.Series([1.0, 2.0, 3.0])
    # Not enough bars for a 150-window: last value is NaN.
    assert math.isnan(sma(closes, 150).iloc[-1])


def test_sma_rejects_nonpositive_period() -> None:
    with pytest.raises(ValueError):
        sma(pd.Series([1.0, 2.0]), 0)
