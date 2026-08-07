"""Average True Range with Wilder smoothing (RMA). Pure (FR-3).

Per PRD §4.2:

    TR    = max(high - low, abs(high - prev_close), abs(low - prev_close))
    ATR14 = Wilder RMA of TR over 14 periods

Seed the RMA with the simple mean of the first ``period`` TR values, then

    ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period

This must match TradingView's ``atr(14)`` output (SC-3).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(ohlc: pd.DataFrame) -> pd.Series:
    """True Range series. The first bar has no previous close, so its TR is NaN — Wilder
    smoothing begins from the first bar that has a defined prior close."""
    high = ohlc["high"]
    low = ohlc["low"]
    prev_close = ohlc["close"].shift(1)

    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    tr = ranges.max(axis=1)
    tr.iloc[0] = np.nan  # no prev_close for the first bar
    return tr


def atr(ohlc: pd.DataFrame, period: int) -> pd.Series:
    """ATR with Wilder smoothing.

    ``ohlc`` must have columns ``high``, ``low``, ``close`` indexed in chronological order.
    Returns a Series aligned to ``ohlc``. The seed sits at index position ``period`` (the
    first bar for which ``period`` TR values are available), and is the simple mean of TR
    over positions 1..period. Earlier positions are NaN.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")

    tr = true_range(ohlc)
    # TR is defined from position 1 onward (position 0 is NaN). We need ``period`` TR values
    # to seed, so the first ATR sits at position ``period``.
    result = pd.Series(np.nan, index=ohlc.index, dtype="float64")

    tr_values = tr.to_numpy()
    n = len(tr_values)
    if n <= period:
        return result  # not enough bars to seed

    seed = np.nanmean(tr_values[1 : period + 1])
    atr_values = result.to_numpy()
    atr_values[period] = seed
    for i in range(period + 1, n):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + tr_values[i]) / period

    return pd.Series(atr_values, index=ohlc.index, dtype="float64")
