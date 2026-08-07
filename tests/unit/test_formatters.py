from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from screener.domain.models import (
    Diff,
    Indicators,
    PriceMode,
    ScanContext,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    SymbolStatus,
)
from screener.screener.formatters import format_scan_message


def _ctx() -> ScanContext:
    return ScanContext(
        scan_type=ScanType.OPEN,
        scan_id="2026-08-07T09:45Z#OPEN",
        ran_at=datetime(2026, 8, 7, 13, 45, tzinfo=UTC),  # 09:45 ET
        trading_day=date(2026, 8, 7),
        indicator_asof=date(2026, 8, 6),
        price_mode=PriceMode.REGULAR,
        is_first_of_day=False,
    )


def _result(sym: str, status: SymbolStatus, price: str | None, dist: str | None,
            in_range: bool) -> SymbolScanResult:
    ind = Indicators(Decimal("196.9"), Decimal("3.1"), date(2026, 8, 6)) if price else None
    return SymbolScanResult(
        symbol=sym,
        status=status,
        price=Decimal(price) if price else None,
        indicators=ind,
        distance_atr=Decimal(dist) if dist else None,
        in_range=in_range,
    )


def test_message_has_header_time_and_sorted_in_range() -> None:
    results = [
        _result("NVDA", SymbolStatus.OK, "122.80", "1.38", True),
        _result("AAPL", SymbolStatus.OK, "198.40", "0.21", True),
    ]
    summary = ScanSummary(
        scan_id="2026-08-07T09:45Z#OPEN", scan_type=ScanType.OPEN,
        scheduled_at=_ctx().ran_at, ran_at=_ctx().ran_at, trading_day=date(2026, 8, 7),
        status=ScanStatus.OK, symbols_scanned=2, in_range=("AAPL", "NVDA"),
        error_symbols=(), insufficient_symbols=(),
    )
    diff = Diff(current=frozenset({"AAPL", "NVDA"}), previous=frozenset({"AAPL"}),
                should_send=True)
    msg = format_scan_message(context=_ctx(), summary=summary, results=results, diff=diff)

    assert "MA150 Screener — OPEN scan" in msg
    assert "09:45 ET" in msg
    # Closest to the MA (AAPL +0.21) must come before NVDA (+1.38).
    assert msg.index("AAPL") < msg.index("NVDA")
    assert "Entered: NVDA" in msg
    assert "2 in range · 2 scanned" in msg


def test_footer_lists_non_ok_symbols() -> None:
    results = [_result("AAPL", SymbolStatus.OK, "198.40", "0.21", True)]
    summary = ScanSummary(
        scan_id="s", scan_type=ScanType.OPEN, scheduled_at=_ctx().ran_at, ran_at=_ctx().ran_at,
        trading_day=date(2026, 8, 7), status=ScanStatus.OK, symbols_scanned=4,
        in_range=("AAPL",), error_symbols=("PLTR", "SOFI"), insufficient_symbols=("ARM",),
    )
    diff = Diff(current=frozenset({"AAPL"}), previous=frozenset({"AAPL"}), should_send=True)
    msg = format_scan_message(context=_ctx(), summary=summary, results=results, diff=diff)
    assert "No data: PLTR, SOFI" in msg
    assert "Insufficient history: ARM" in msg


def test_stale_price_gets_tilde_prefix() -> None:
    results = [_result("AAPL", SymbolStatus.STALE_PRICE, "198.40", "0.21", True)]
    summary = ScanSummary(
        scan_id="s", scan_type=ScanType.PRE, scheduled_at=_ctx().ran_at, ran_at=_ctx().ran_at,
        trading_day=date(2026, 8, 7), status=ScanStatus.OK, symbols_scanned=1,
        in_range=("AAPL",), error_symbols=(), insufficient_symbols=(),
    )
    diff = Diff(current=frozenset({"AAPL"}), previous=frozenset({"AAPL"}), should_send=True)
    msg = format_scan_message(context=_ctx(), summary=summary, results=results, diff=diff)
    assert "~$198.40" in msg


def test_no_footer_when_all_healthy() -> None:
    results = [_result("AAPL", SymbolStatus.OK, "198.40", "0.21", True)]
    summary = ScanSummary(
        scan_id="s", scan_type=ScanType.OPEN, scheduled_at=_ctx().ran_at, ran_at=_ctx().ran_at,
        trading_day=date(2026, 8, 7), status=ScanStatus.OK, symbols_scanned=1,
        in_range=("AAPL",), error_symbols=(), insufficient_symbols=(),
    )
    diff = Diff(current=frozenset({"AAPL"}), previous=frozenset({"AAPL"}), should_send=True)
    msg = format_scan_message(context=_ctx(), summary=summary, results=results, diff=diff)
    assert "⚠️" not in msg
