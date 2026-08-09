"""Alpha Vantage ``NEWS_SENTIMENT`` implementation of ``NewsProvider`` (F7T01). A richer news
source than Finnhub: each item carries a fuller ``summary`` and a per-ticker sentiment label, which
is what the AI read (F6/F7) actually needs — the F6 ``web_fetch`` approach was defeated by
bot-blocked publisher pages, so summaries now carry the substance instead.

Same resilience contract as the Finnhub adapter: network behind an injectable ``http_get_fn`` seam
(contract tests replay recorded frames, no network), retried internally. A present-but-empty
``feed`` is honest ("no recent news") and returns ``[]``; a network outage *or* an Alpha Vantage
error/rate-limit body (``Information``/``Note``/``Error Message`` with no ``feed``) raises
``ProviderError`` so the assembler can fall back. Items are deduped (by URL), sorted newest-first,
and capped at ``max_items``.
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

_URL = "https://www.alphavantage.co/query"
_TIME_FMT = "%Y%m%dT%H%M%S"  # Alpha Vantage `time_published`, e.g. "20260809T143000"


def _default_get(url: str, params: dict[str, str], timeout: float) -> Any:
    import httpx

    return httpx.get(url, params=params, timeout=timeout).json()


class AlphaVantageNewsProvider:
    source = "alphavantage"  # labels the cache entry (PRD §10)

    def __init__(
        self,
        api_key: str,
        *,
        max_items: int = 10,
        http_get_fn: HttpGetFn | None = None,
        timeout: float = 10.0,
        retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._max_items = max_items
        self._get = http_get_fn or _default_get
        self._timeout = timeout
        self._retries = retries
        self._backoff_base = backoff_base
        self._sleep = sleep_fn

    def fetch_company_news(self, symbol: str, since: date) -> list[NewsItem]:
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "time_from": since.strftime("%Y%m%dT0000"),
            "sort": "LATEST",
            "limit": str(max(self._max_items, 50)),  # AV floor is 50; we cap ourselves below
            "apikey": self._key,
        }
        payload = self._fetch(params)
        if not isinstance(payload, dict):
            raise ProviderError(f"Alpha Vantage news failed for {symbol}")
        feed = payload.get("feed")
        if feed is None:
            # No feed key -> a rate-limit / bad-key / bad-params body (Information/Note/Error
            # Message). Treat as an outage so the assembler falls back to yfinance.
            raise ProviderError(f"Alpha Vantage news failed for {symbol}: {_reason(payload)}")
        rows = feed if isinstance(feed, list) else []

        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in rows:
            item = _to_item(row, symbol)
            if item is None or item.published_at.date() < since or item.url in seen:
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


def _to_item(row: Any, symbol: str) -> NewsItem | None:
    if not isinstance(row, dict):
        return None
    url = row.get("url")
    title = row.get("title")
    published_raw = row.get("time_published")
    if not url or not title or not published_raw:
        return None
    try:
        published = datetime.strptime(str(published_raw), _TIME_FMT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return NewsItem(
        published_at=published,
        source=str(row.get("source") or "Alpha Vantage"),
        headline=str(title),
        url=str(url),
        summary=_compose_summary(row, symbol),
    )


def _compose_summary(row: dict[str, Any], symbol: str) -> str | None:
    """Fold Alpha Vantage's article summary and the ticker's sentiment label into one string, so the
    sentiment signal reaches the AI read without widening the ``NewsItem`` dataclass."""
    summary = row.get("summary")
    text = str(summary).strip() if summary else ""
    label = _sentiment_label(row, symbol)
    if text and label:
        return f"{text} (sentiment: {label})"
    if text:
        return text
    if label:
        return f"(sentiment: {label})"
    return None


def _sentiment_label(row: dict[str, Any], symbol: str) -> str | None:
    ticker_sentiment = row.get("ticker_sentiment")
    if isinstance(ticker_sentiment, list):
        for entry in ticker_sentiment:
            if isinstance(entry, dict) and str(entry.get("ticker", "")).upper() == symbol.upper():
                label = entry.get("ticker_sentiment_label")
                if label:
                    return str(label)
    overall = row.get("overall_sentiment_label")
    return str(overall) if overall else None


def _reason(payload: dict[str, Any]) -> str:
    for key in ("Error Message", "Information", "Note"):
        if payload.get(key):
            return str(payload[key])
    return "no feed returned"
