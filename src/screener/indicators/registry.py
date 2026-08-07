"""Name -> indicator callable, for Phase 2 reuse. Phase 1 registers exactly the two indicators
the MA150 proximity criterion needs; a second criterion (RSI, volume surge, …) adds an entry
here plus a pure function alongside sma/atr, with no change to the pipeline (PRD §10)."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from screener.indicators.atr import atr
from screener.indicators.sma import sma

# Each callable takes a price frame/series plus a period and returns an aligned Series.
INDICATORS: dict[str, Callable[..., pd.Series]] = {
    "sma": sma,
    "atr": atr,
}
