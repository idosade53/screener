"""Basic XNYS calendar correctness. The full weekend/holiday/half-day frozen-clock matrix is
M6; here we confirm the adapter answers the port correctly for a handful of known sessions."""

from __future__ import annotations

from datetime import date, time

import pytest

from screener.adapters.calendar.xnys_calendar import XnysCalendar


@pytest.fixture(scope="module")
def cal() -> XnysCalendar:
    return XnysCalendar()


def test_regular_weekday_is_trading_day(cal: XnysCalendar) -> None:
    assert cal.is_trading_day(date(2026, 8, 7)) is True  # Friday


def test_weekend_is_not_trading_day(cal: XnysCalendar) -> None:
    assert cal.is_trading_day(date(2026, 8, 8)) is False  # Saturday
    assert cal.is_trading_day(date(2026, 8, 9)) is False  # Sunday


def test_new_years_day_is_holiday(cal: XnysCalendar) -> None:
    assert cal.is_trading_day(date(2026, 1, 1)) is False


def test_previous_trading_day_skips_weekend(cal: XnysCalendar) -> None:
    # Monday 2026-08-10 -> previous session is Friday 2026-08-07.
    assert cal.previous_trading_day(date(2026, 8, 10)) == date(2026, 8, 7)


def test_regular_session_closes_at_16_00_et(cal: XnysCalendar) -> None:
    assert cal.session_close(date(2026, 8, 7)) == time(16, 0)


def test_half_day_closes_early(cal: XnysCalendar) -> None:
    # Day after Thanksgiving 2024 was a half-day: early close 13:00 ET.
    assert cal.session_close(date(2024, 11, 29)) == time(13, 0)
