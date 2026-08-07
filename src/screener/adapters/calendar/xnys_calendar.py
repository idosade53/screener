"""TradingCalendar over ``exchange_calendars`` XNYS. Handles NYSE holidays and half-days.

Basic correctness is exercised here; the full weekend/holiday/half-day frozen-clock matrix is
M6. DST for scheduling is handled upstream (EventBridge named-tz cron / APScheduler), not here —
this port only answers trading-day and session-close questions (architecture §8.1)."""

from __future__ import annotations

from datetime import date, time

import exchange_calendars as xcals
import pandas as pd


class XnysCalendar:
    def __init__(self) -> None:
        self._cal = xcals.get_calendar("XNYS")

    def is_trading_day(self, d: date) -> bool:
        return bool(self._cal.is_session(pd.Timestamp(d)))

    def previous_trading_day(self, d: date) -> date:
        ts = self._cal.previous_session(pd.Timestamp(d))
        return date(ts.year, ts.month, ts.day)

    def session_close(self, d: date) -> time:
        """Regular-session close in exchange-local time (ET). 13:00 on half-days."""
        close_ts = self._cal.session_close(pd.Timestamp(d))
        # exchange_calendars returns tz-aware UTC; convert to exchange tz for wall-clock ET.
        local = close_ts.tz_convert(self._cal.tz)
        return time(local.hour, local.minute)
