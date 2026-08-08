"""Finnhub implementation of ``NewsProvider`` (PRD §8, FR-3). Primary company-news feed; the
yfinance ``.news`` fallback lives in ``yfinance_provider.py`` and is selected by the assembler.

Same resilience contract as the fundamentals adapters: network behind an injectable ``http_get_fn``
seam (contract tests replay recorded frames, no network), retried internally. An *empty* result is
honest ("no recent news") and returns ``[]``; a network *outage* raises ``ProviderError`` so the
assembler can fall back. Items are deduped (by URL), sorted newest-first and capped at
``max_items``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from screener.domain.errors import ProviderError
from screener.domain.models import NewsItem

# (url, params, timeout_seconds) -> parsed JSON body. Injected for testability.
HttpGetFn = Callable[[str, dict[str, str], float], Any]

_URL = "https://finnhub.io/api/v1/company-news"


def _default_get(url: str, params: dict[str, str], timeout: float) -> Any:
    import httpx

    return httpx.get(url, params=params, timeout=timeout).json()


class FinnhubNewsProvider:
    source = "finnhub"  # labels the cache entry (PRD §10)

    def __init__(
        self,
        api_key: str,
        *,
        max_items: int = 10,
        http_get_fn: HttpGetFn | None = None,
        today_fn: Callable[[], date] = date.today,
        timeout: float = 10.0,
        retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._max_items = max_items
        self._get = http_get_fn or _default_get
        self._today = today_fn
        self._timeout = timeout
        self._retries = retries
        self._backoff_base = backoff_base
        self._sleep = sleep_fn

    def fetch_company_news(self, symbol: str, since: date) -> list[NewsItem]:
        params = {
            "symbol": symbol,
            "from": since.isoformat(),
            "to": self._today().isoformat(),
            "token": self._key,
        }
        payload = self._fetch(params)
        if payload is None:
            raise ProviderError(f"Finnhub company-news failed for {symbol}")
        rows = payload if isinstance(payload, list) else []

        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in rows:
            item = _to_item(row)
            if item is None or item.url in seen:
                continue
            seen.add(item.url)
            items.append(item)
        items.sort(key=lambda i: i.published_at, reverse=True)
        return items[: self._max_items]

    def _fetch(self, params: dict[str, str]) -> Any:
        for attempt in range(self._retries):
            try:
                return self._get(_URL, params, self._timeout)
            except Exception:  # noqa: BLE001 — retry transient failures internally
                if attempt < self._retries - 1:
                    self._sleep(self._backoff_base * (2**attempt))
        return None


def _to_item(row: Any) -> NewsItem | None:
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    headline = row.get("headline")
    ts = row.get("datetime")
    if not url or not headline or ts is None:
        return None
    try:
        published = datetime.fromtimestamp(int(ts), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None
    return NewsItem(
        published_at=published,
        source=str(row.get("source") or "Finnhub"),
        headline=str(headline),
        url=str(url),
        summary=(str(row["summary"]) if row.get("summary") else None),
    )
