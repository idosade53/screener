"""Company-news access behind a port (Finnhub primary, yfinance ``.news`` fallback — PRD §8,
FR-3). Same resilience contract as ``fundamentals``/``notifier``: an implementation never
raises past the adapter; on outage it degrades to the fallback or returns an empty list, and
the caller notes it in the footer."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from screener.domain.models import NewsItem


class NewsProvider(Protocol):
    def fetch_company_news(self, symbol: str, since: date) -> list[NewsItem]:
        """Headlines from ``since`` onward, deduped and sorted newest-first, capped by the
        caller's ``news_max_items`` (FR-3)."""
        ...
