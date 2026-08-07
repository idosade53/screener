"""Bridge from a bar series to a single ``Indicators`` value. Lives in ``indicators/`` so the
float boundary stays confined here (architecture §4): bars come in as Decimal, computation runs
in float via pandas, and the result is quantised back to Decimal.

This same function serves the cache-fill path (CLOSE), the cache-miss recompute path, and
Phase 2's back-tester over historical bars (§7.4) — which is why it must stay pure."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from screener.domain.models import Bar, Indicators
from screener.indicators.atr import atr
from screener.indicators.quantize import to_decimal_4dp
from screener.indicators.sma import sma


def latest_indicators(
    bars: Sequence[Bar], sma_period: int, atr_period: int, min_bars: int
) -> Indicators | None:
    """Compute SMA and ATR at the most recent bar. Returns ``None`` when there is insufficient
    history (< ``min_bars``), which the caller maps to INSUFFICIENT_DATA."""
    if len(bars) < min_bars:
        return None

    ordered = sorted(bars, key=lambda b: b.date)
    frame = pd.DataFrame(
        {
            "high": [float(b.high) for b in ordered],
            "low": [float(b.low) for b in ordered],
            "close": [float(b.close) for b in ordered],
        }
    )

    sma_series = sma(frame["close"], sma_period)
    atr_series = atr(frame, atr_period)

    sma_last = sma_series.iloc[-1]
    atr_last = atr_series.iloc[-1]
    if pd.isna(sma_last) or pd.isna(atr_last):
        return None

    return Indicators(
        sma150=to_decimal_4dp(float(sma_last)),
        atr14=to_decimal_4dp(float(atr_last)),
        asof=ordered[-1].date,
    )
