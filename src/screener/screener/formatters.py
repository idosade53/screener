"""Message formatting (PRD FR-5). This is the only place a timezone appears in output: the user
reads ET even though they are in IDT (architecture §8.1). Full polish (chunking at 4096, exact
column alignment) belongs to the Telegram adapter/M4; this produces the digest body."""

from __future__ import annotations

from collections.abc import Sequence
from zoneinfo import ZoneInfo

from screener.domain.models import (
    Diff,
    ScanContext,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    SymbolStatus,
)

_ET = ZoneInfo("America/New_York")


def _abs(value: object) -> float:
    return abs(float(value))  # type: ignore[arg-type]


def format_scan_message(
    *,
    context: ScanContext,
    summary: ScanSummary,
    results: Sequence[SymbolScanResult],
    diff: Diff,
) -> str:
    et = context.ran_at.astimezone(_ET)
    header = f"📊 MA150 Screener — {context.scan_type.value} scan"
    when = f"{et:%a %d %b, %H:%M} ET"

    in_range = [r for r in results if r.in_range]
    in_range.sort(key=lambda r: _abs(r.distance_atr) if r.distance_atr is not None else 1e9)

    lines = [header, when, "", "In range (|P − MA150| ≤ band·ATR):"]
    if in_range:
        for r in in_range:
            lines.append(_format_row(r))
    else:
        lines.append("  (none)")

    entered = sorted(diff.entered)
    exited = sorted(diff.exited)
    if entered:
        lines.append(f"\nEntered: {', '.join(entered)}")
    if exited:
        lines.append(f"Exited:  {', '.join(exited)}")

    lines.append(f"\n{len(in_range)} in range · {summary.symbols_scanned} scanned")

    footer = _failure_footer(summary)
    if footer:
        lines.append(footer)

    # The CLOSE message doubles as the daily proof-of-life heartbeat (FR-7).
    if context.scan_type is ScanType.CLOSE:
        lines.append("💓 Daily heartbeat")

    return "\n".join(lines)


def _format_row(r: SymbolScanResult) -> str:
    assert r.indicators is not None and r.price is not None and r.distance_atr is not None
    stale = "~" if r.status is SymbolStatus.STALE_PRICE else ""
    dist = float(r.distance_atr)
    sign = "+" if dist >= 0 else "−"
    return (
        f"  {r.symbol:<6} {sign}{abs(dist):.2f} ATR   "
        f"{stale}${float(r.price):.2f}  (MA {float(r.indicators.sma150):.2f})"
    )


def _failure_footer(summary: ScanSummary) -> str | None:
    # The footer appears in every message whenever any symbol is in a non-OK state (Q3), not
    # only on transition. Omitted entirely when everything is healthy.
    parts = []
    if summary.error_symbols:
        parts.append(f"No data: {', '.join(sorted(summary.error_symbols))}")
    if summary.insufficient_symbols:
        parts.append(f"Insufficient history: {', '.join(sorted(summary.insufficient_symbols))}")
    return "⚠️ " + " · ".join(parts) if parts else None
