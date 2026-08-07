"""ATR unit tests.

Three layers of verification:
1. Hand-computed Wilder RMA over a small period-3 series (exact arithmetic, checkable by hand).
2. Invariants (constant-TR series -> constant ATR; convergence).
3. Cross-check against an independent reference RMA implementation over a long deterministic
   series with period=14 (the production period), long enough that Wilder smoothing has
   converged (architecture §10, risk R6 — a wrong seed diverges slowly).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from screener.indicators.atr import atr, true_range


def _frame(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_true_range_first_bar_is_nan() -> None:
    df = _frame([10.5, 11.5], [9.5, 10.2], [10.0, 11.0])
    tr = true_range(df)
    assert math.isnan(tr.iloc[0])
    # Bar1: max(H-L=1.3, |11.5-10|=1.5, |10.2-10|=0.2) = 1.5
    assert tr.iloc[1] == pytest.approx(1.5)


def test_atr_hand_computed_period_3() -> None:
    # Closes chosen so TR values are 1.5, 1.5, 1.5, 2.2, 1.5 at positions 1..5.
    highs = [10.5, 11.5, 12.5, 11.8, 13.2, 14.5]
    lows = [9.5, 10.2, 11.0, 10.5, 12.1, 13.0]
    closes = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0]
    result = atr(_frame(highs, lows, closes), 3)

    # Positions 0..2 have no seeded ATR yet.
    assert math.isnan(result.iloc[2])
    # Seed at position 3 = mean(TR[1..3]) = mean(1.5, 1.5, 1.5) = 1.5
    assert result.iloc[3] == pytest.approx(1.5)
    # ATR[4] = (1.5*2 + 2.2)/3 = 5.2/3
    assert result.iloc[4] == pytest.approx(5.2 / 3)
    # ATR[5] = (ATR[4]*2 + 1.5)/3
    assert result.iloc[5] == pytest.approx(((5.2 / 3) * 2 + 1.5) / 3)


def test_atr_constant_true_range_is_constant() -> None:
    # Every bar has H-L = 2.0 and closes flat, so every TR = 2.0 and ATR converges to 2.0.
    n = 60
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    result = atr(_frame(highs, lows, closes), 14)
    assert result.iloc[-1] == pytest.approx(2.0)


def test_atr_insufficient_bars_is_all_nan() -> None:
    highs = [10.0] * 10
    lows = [9.0] * 10
    closes = [9.5] * 10
    result = atr(_frame(highs, lows, closes), 14)
    assert result.isna().all()


def _reference_wilder_atr(df: pd.DataFrame, period: int) -> np.ndarray:
    """Independent reference implementation of the same Wilder RMA spec (PRD §4.2), written
    differently from the production code, to catch algorithmic mistakes."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    n = len(close)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    out = np.full(n, np.nan)
    if n > period:
        out[period] = sum(tr[1 : period + 1]) / period
        for i in range(period + 1, n):
            out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def test_atr_matches_reference_over_long_deterministic_series() -> None:
    rng = np.random.default_rng(42)
    n = 400  # well past convergence for period=14
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.1, 2.0, n)
    low = close - rng.uniform(0.1, 2.0, n)
    df = pd.DataFrame({"high": high, "low": low, "close": close})

    got = atr(df, 14).to_numpy()
    want = _reference_wilder_atr(df, 14)

    # Compare only the defined (post-seed) region.
    mask = ~np.isnan(want)
    np.testing.assert_allclose(got[mask], want[mask], rtol=0, atol=1e-9)


def test_atr_rejects_nonpositive_period() -> None:
    with pytest.raises(ValueError):
        atr(_frame([1.0], [1.0], [1.0]), 0)
