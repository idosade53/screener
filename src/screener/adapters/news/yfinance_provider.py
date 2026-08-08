"""yfinance ``.news`` fallback for ``NewsProvider`` (PRD §8, FR-3): zero-key, reusing the existing
yfinance dependency. Selected by the assembler when Finnhub fails. Network seam is an injectable
``news_fn`` so contract tests replay a recorded list with no network."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

from screener.domain.models import NewsItem


def _default_news(symbol: str) -> list[dict[str, Any]]:
    import yfinance as yf

    return cast(list[dict[str, Any]], yf.Ticker(symbol).news or [])


class YFinanceNewsProvider:
    source = "yfinance"  # labels the cache entry (PRD §10)

    def __init__(
        self,
        *,
        max_items: int = 10,
        news_fn: Callable[[str], list[dict[str, Any]]] = _default_news,
    ) -> None:
        self._max_items = max_items
        self._news_fn = news_fn

    def fetch_company_news(self, symbol: str, since: date) -> list[NewsItem]:
        rows = self._news_fn(symbol) or []
        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in rows:
            item = _to_item(row)
            if item is None or item.published_at.date() < since or item.url in seen:
                continue
            seen.add(item.url)
            items.append(item)
        items.sort(key=lambda i: i.published_at, reverse=True)
        return items[: self._max_items]


def _to_item(row: Any) -> NewsItem | None:
    # yfinance has used both a flat shape ({title, publisher, link, providerPublishTime}) and a
    # nested one ({content: {title, provider, canonicalUrl, pubDate}}). Tolerate both.
    if not isinstance(row, dict):
        return None
    nested = row.get("content")
    content: dict[str, Any] = nested if isinstance(nested, dict) else row
    title = content.get("title")
    url = content.get("link") or content.get("canonicalUrl") or row.get("link")
    if isinstance(url, dict):
        url = url.get("url")
    published = _published(content, row)
    if not title or not url or published is None:
        return None
    provider = content.get("publisher") or content.get("provider")
    if isinstance(provider, dict):
        provider = provider.get("displayName")
    return NewsItem(
        published_at=published,
        source=str(provider or "yfinance"),
        headline=str(title),
        url=str(url),
        summary=(str(content["summary"]) if content.get("summary") else None),
    )


def _published(content: dict[str, Any], row: dict[str, Any]) -> datetime | None:
    epoch = row.get("providerPublishTime")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=UTC)
        except (ValueError, TypeError, OSError):
            return None
    iso = content.get("pubDate") or content.get("displayTime")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
