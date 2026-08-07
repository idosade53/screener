"""Change detection (FR-4, Q1). Operates on an opaque set of symbol strings, so it is unchanged
when a second criterion lands — the set simply becomes "symbols passing the configured
combination" (architecture §11.1)."""

from __future__ import annotations

from collections.abc import Iterable

from screener.domain.models import Diff, ScanType


def compute_diff(
    *,
    current_in_range: Iterable[str],
    previous_in_range: Iterable[str],
    scan_type: ScanType,
    is_first_of_day: bool,
) -> Diff:
    current = frozenset(current_in_range)
    previous = frozenset(previous_in_range)
    should_send = (
        scan_type is ScanType.MANUAL  # /scan always reports
        or is_first_of_day  # daily baseline (Q1)
        or current != previous  # the set changed
    )
    return Diff(current=current, previous=previous, should_send=should_send)
