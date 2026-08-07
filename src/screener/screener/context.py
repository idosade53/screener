"""Resolves a ScanType into a ScanContext (architecture §6, stage 1). Constructed once at the
top of a scan; no stage below calls the clock again (ADR A4).

The critical rule (§4.3): indicators are always computed from *completed* daily bars, so
``indicator_asof`` never points at a still-forming session.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time
from zoneinfo import ZoneInfo

from screener.domain.models import PriceMode, ScanContext, ScanType
from screener.ports.calendar import TradingCalendar

_ET = ZoneInfo("America/New_York")

_PRICE_MODE: dict[ScanType, PriceMode] = {
    ScanType.PRE: PriceMode.PREMARKET,
    ScanType.OPEN: PriceMode.REGULAR,
    ScanType.CLOSE: PriceMode.OFFICIAL_CLOSE,
    ScanType.MANUAL: PriceMode.REGULAR,
}


def resolve_context(
    *,
    scan_type: ScanType,
    now: datetime,
    calendar: TradingCalendar,
    is_first_of_day: bool,
    scheduled_times: Mapping[ScanType, time],
) -> ScanContext:
    et_now = now.astimezone(_ET)
    today = et_now.date()

    # trading_day: the ET session this scan belongs to. Scheduled scans only fire on trading
    # days (the pipeline asserts this and exits early otherwise). A MANUAL scan may run any
    # time, so it attaches to today if today trades, else to the previous session.
    if scan_type is ScanType.MANUAL and not calendar.is_trading_day(today):
        trading_day = calendar.previous_trading_day(today)
    else:
        trading_day = today

    price_mode = _PRICE_MODE[scan_type]

    if scan_type is ScanType.CLOSE:
        indicator_asof = trading_day  # today's bar is complete post-close
    elif scan_type in (ScanType.PRE, ScanType.OPEN):
        indicator_asof = calendar.previous_trading_day(trading_day)
    else:  # MANUAL: use today's completed bar only if the session has already closed
        after_close = (
            calendar.is_trading_day(today)
            and et_now.time() >= calendar.session_close(today)
        )
        if after_close:
            indicator_asof = today
            price_mode = PriceMode.OFFICIAL_CLOSE
        else:
            indicator_asof = calendar.previous_trading_day(trading_day)

    scan_id = _scan_id(scan_type, trading_day, now, scheduled_times)

    return ScanContext(
        scan_type=scan_type,
        scan_id=scan_id,
        ran_at=now,
        trading_day=trading_day,
        indicator_asof=indicator_asof,
        price_mode=price_mode,
        is_first_of_day=is_first_of_day,
    )


def _scan_id(
    scan_type: ScanType,
    trading_day: object,
    now: datetime,
    scheduled_times: Mapping[ScanType, time],
) -> str:
    # Scheduled scans get a deterministic id keyed to the scheduled time, so a retried
    # invocation claims the same id (idempotency, §8.4). MANUAL scans use a wall-clock id and
    # never collide.
    if scan_type is ScanType.MANUAL:
        return f"{now.astimezone(ZoneInfo('UTC')):%Y-%m-%dT%H:%M:%SZ}#MANUAL"
    hhmm = scheduled_times[scan_type].strftime("%H:%M")
    return f"{trading_day}T{hhmm}Z#{scan_type.value}"
