from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from screener.domain.models import PriceMode, ScanType
from screener.screener.context import resolve_context

_SCHEDULED = {ScanType.PRE: time(9, 0), ScanType.OPEN: time(9, 45), ScanType.CLOSE: time(20, 15)}


class _WeekdayCalendar:
    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5

    def previous_trading_day(self, d: date) -> date:
        cur = d - timedelta(days=1)
        while cur.weekday() >= 5:
            cur -= timedelta(days=1)
        return cur

    def session_close(self, d: date) -> time:
        return time(16, 0)


def _resolve(scan_type: ScanType, now: datetime, first: bool = True):
    return resolve_context(
        scan_type=scan_type,
        now=now,
        calendar=_WeekdayCalendar(),
        is_first_of_day=first,
        scheduled_times=_SCHEDULED,
    )


def test_close_uses_todays_completed_bar() -> None:
    # Thursday 2026-08-06, 20:15 ET == 2026-08-07 00:15 UTC.
    now = datetime(2026, 8, 7, 0, 15, tzinfo=UTC)
    ctx = _resolve(ScanType.CLOSE, now)
    assert ctx.trading_day == date(2026, 8, 6)
    assert ctx.indicator_asof == date(2026, 8, 6)
    assert ctx.price_mode is PriceMode.OFFICIAL_CLOSE
    assert ctx.scan_id == "2026-08-06T20:15Z#CLOSE"


def test_pre_uses_previous_close() -> None:
    # Thursday 2026-08-06, 09:00 ET == 13:00 UTC.
    now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)
    ctx = _resolve(ScanType.PRE, now)
    assert ctx.trading_day == date(2026, 8, 6)
    assert ctx.indicator_asof == date(2026, 8, 5)  # Wednesday
    assert ctx.price_mode is PriceMode.PREMARKET


def test_open_uses_previous_close() -> None:
    now = datetime(2026, 8, 6, 13, 45, tzinfo=UTC)  # 09:45 ET
    ctx = _resolve(ScanType.OPEN, now)
    assert ctx.indicator_asof == date(2026, 8, 5)
    assert ctx.price_mode is PriceMode.REGULAR


def test_previous_trading_day_skips_weekend_for_pre() -> None:
    # Monday 2026-08-10, 09:00 ET -> previous session is Friday 2026-08-07.
    now = datetime(2026, 8, 10, 13, 0, tzinfo=UTC)
    ctx = _resolve(ScanType.PRE, now)
    assert ctx.indicator_asof == date(2026, 8, 7)


def test_manual_after_close_uses_today() -> None:
    # Thursday 2026-08-06, 21:00 ET == 2026-08-07 01:00 UTC (after 16:00 close).
    now = datetime(2026, 8, 7, 1, 0, tzinfo=UTC)
    ctx = _resolve(ScanType.MANUAL, now)
    assert ctx.trading_day == date(2026, 8, 6)
    assert ctx.indicator_asof == date(2026, 8, 6)
    assert ctx.price_mode is PriceMode.OFFICIAL_CLOSE
    assert ctx.scan_id.endswith("#MANUAL")


def test_manual_before_close_uses_previous() -> None:
    # Thursday 2026-08-06, 11:00 ET == 15:00 UTC (before close).
    now = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)
    ctx = _resolve(ScanType.MANUAL, now)
    assert ctx.indicator_asof == date(2026, 8, 5)
    assert ctx.price_mode is PriceMode.REGULAR


def test_manual_on_weekend_attaches_to_previous_session() -> None:
    # Saturday 2026-08-08, noon ET.
    now = datetime(2026, 8, 8, 16, 0, tzinfo=UTC)
    ctx = _resolve(ScanType.MANUAL, now)
    assert ctx.trading_day == date(2026, 8, 7)  # Friday
