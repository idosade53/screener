"""Simple moving average. Pure: no I/O, no globals (FR-3). ``float`` internally where pandas
requires it; the caller quantises to Decimal at the module edge."""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average of ``series`` over ``period`` completed values.

    Returns a Series aligned to ``series``; the first ``period - 1`` entries are NaN.
    ``series`` is expected to be the closing-price series over completed daily bars.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    return series.rolling(window=period, min_periods=period).mean()
