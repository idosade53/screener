"""Split detection (architecture §7.3). Pure: compares stored vs freshly-fetched closes over an
overlapping window. Bars are stored unadjusted (FR-2), so a split shows up as a discontinuity
between what we stored before the split and what the provider now reports.

On detection the pipeline deletes and refetches the full window, invalidates the indicator cache,
and marks the symbol DATA_ERROR for that one scan (one missed observation is cheaper than one
wrong one). A missed ratio is caught by the same check the next day, since the discontinuity
persists in stored data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from screener.domain.models import Bar

# Common split/reverse-split ratios to match against (new/old close). 2:1 split halves the price
# (ratio 0.5); 1:10 reverse split multiplies by 10.
_SIMPLE_RATIOS = (
    0.5, 1 / 3, 2 / 3, 0.25, 0.75, 0.2, 0.1, 1 / 7,  # forward splits
    2.0, 3.0, 4.0, 5.0, 7.0, 10.0,  # reverse splits
)
_RELATIVE_TOL = 0.02  # 2% deviation from 1.0 required before we suspect a split
_RATIO_TOL = 0.02  # how close the observed ratio must be to a simple fraction


def _close_by_date(bars: Sequence[Bar]) -> dict[date, Decimal]:
    return {b.date: b.close for b in bars}


def detect_split(stored: Sequence[Bar], fetched: Sequence[Bar]) -> bool:
    """True if the overlapping closes differ by a factor that resembles a simple split ratio.

    A large gap-down is *not* a split: it moves one bar, not the whole prior series, so the
    ratio on the overlapping (pre-event) bars stays ~1.0 (architecture §7.3).
    """
    stored_by_date = _close_by_date(stored)
    fetched_by_date = _close_by_date(fetched)
    common = sorted(set(stored_by_date) & set(fetched_by_date))
    if not common:
        return False

    for d in common:
        old = stored_by_date[d]
        new = fetched_by_date[d]
        if old == 0:
            continue
        ratio = float(new / old)
        if abs(ratio - 1.0) <= _RELATIVE_TOL:
            continue  # this bar is unchanged; not a split point
        if any(abs(ratio - r) <= _RATIO_TOL for r in _SIMPLE_RATIOS):
            return True
    return False
