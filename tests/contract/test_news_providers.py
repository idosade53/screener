"""News adapters over recorded frames — no network (PRD FR-3, SC-4). Finnhub is primary (raises
``ProviderError`` on a network outage so the assembler can fall back); yfinance ``.news`` is the
zero-key fallback."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from screener.adapters.news.alphavantage_provider import AlphaVantageNewsProvider
from screener.adapters.news.finnhub_provider import FinnhubNewsProvider
from screener.adapters.news.yfinance_provider import YFinanceNewsProvider
from screener.domain.errors import ProviderError

_FINNHUB = [
    {
        "datetime": 1735689600,  # 2025-01-01 00:00 UTC
        "headline": "Older headline",
        "source": "Reuters",
        "url": "https://example.com/a",
        "summary": "s1",
    },
    {
        "datetime": 1735776000,  # 2025-01-02 00:00 UTC
        "headline": "Newer headline",
        "source": "Bloomberg",
        "url": "https://example.com/b",
        "summary": "",
    },
    {  # duplicate URL of the first -> deduped
        "datetime": 1735689600,
        "headline": "Dup",
        "source": "Reuters",
        "url": "https://example.com/a",
    },
    {"headline": "no url", "datetime": 1735776000},  # dropped: missing url
]


def test_finnhub_maps_dedups_sorts_and_caps() -> None:
    calls: list[dict[str, str]] = []

    def http(url: str, params: dict[str, str], timeout: float) -> Any:
        calls.append(params)
        return _FINNHUB

    prov = FinnhubNewsProvider(
        "KEY", max_items=5, http_get_fn=http, today_fn=lambda: date(2025, 1, 3)
    )
    items = prov.fetch_company_news("AAPL", since=date(2024, 12, 25))
    # Newest first, duplicate URL dropped.
    assert [i.headline for i in items] == ["Newer headline", "Older headline"]
    assert items[0].source == "Bloomberg"
    assert items[1].summary == "s1"
    assert items[0].summary is None  # empty summary -> None
    assert calls[0]["from"] == "2024-12-25" and calls[0]["to"] == "2025-01-03"


def test_finnhub_cap() -> None:
    prov = FinnhubNewsProvider("KEY", max_items=1, http_get_fn=lambda *_: _FINNHUB)
    assert len(prov.fetch_company_news("AAPL", since=date(2024, 1, 1))) == 1


def test_finnhub_empty_is_not_an_outage() -> None:
    prov = FinnhubNewsProvider("KEY", http_get_fn=lambda *_: [])
    assert prov.fetch_company_news("AAPL", since=date(2024, 1, 1)) == []


def test_finnhub_outage_raises() -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("network")

    prov = FinnhubNewsProvider("KEY", http_get_fn=boom, retries=1, sleep_fn=lambda _: None)
    with pytest.raises(ProviderError):
        prov.fetch_company_news("AAPL", since=date(2024, 1, 1))


# --------------------------------------------------------------- Alpha Vantage (F7T01)
_AV_FEED = {
    "items": "3",
    "feed": [
        {
            "title": "Older AV headline",
            "url": "https://example.com/a",
            "time_published": "20250101T120000",
            "source": "Reuters",
            "summary": "Older story body.",
            "ticker_sentiment": [
                {"ticker": "AAPL", "ticker_sentiment_label": "Somewhat-Bullish"},
                {"ticker": "MSFT", "ticker_sentiment_label": "Bearish"},
            ],
        },
        {
            "title": "Newer AV headline",
            "url": "https://example.com/b",
            "time_published": "20250102T120000",
            "source": "Bloomberg",
            "overall_sentiment_label": "Neutral",  # no per-ticker entry -> fall back to overall
        },
        {  # duplicate URL of the first -> deduped
            "title": "Dup",
            "url": "https://example.com/a",
            "time_published": "20250101T120000",
        },
        {"title": "no url", "time_published": "20250102T120000"},  # dropped: missing url
    ],
}


def test_alphavantage_maps_sentiment_dedups_sorts_and_caps() -> None:
    calls: list[dict[str, str]] = []

    def http(url: str, params: dict[str, str], timeout: float) -> Any:
        calls.append(params)
        return _AV_FEED

    prov = AlphaVantageNewsProvider("KEY", max_items=5, http_get_fn=http)
    items = prov.fetch_company_news("AAPL", since=date(2024, 12, 25))
    # Newest first, duplicate URL dropped, malformed dropped.
    assert [i.headline for i in items] == ["Newer AV headline", "Older AV headline"]
    # Per-ticker sentiment folded into the summary; overall used when no per-ticker entry.
    assert items[1].summary == "Older story body. (sentiment: Somewhat-Bullish)"
    assert items[0].summary == "(sentiment: Neutral)"
    assert calls[0]["tickers"] == "AAPL" and calls[0]["time_from"] == "20241225T0000"


def test_alphavantage_cap() -> None:
    prov = AlphaVantageNewsProvider("KEY", max_items=1, http_get_fn=lambda *_: _AV_FEED)
    assert len(prov.fetch_company_news("AAPL", since=date(2024, 1, 1))) == 1


def test_alphavantage_since_filters_older_items() -> None:
    prov = AlphaVantageNewsProvider("KEY", http_get_fn=lambda *_: _AV_FEED)
    items = prov.fetch_company_news("AAPL", since=date(2025, 1, 2))
    assert [i.headline for i in items] == ["Newer AV headline"]


def test_alphavantage_empty_feed_is_not_an_outage() -> None:
    prov = AlphaVantageNewsProvider("KEY", http_get_fn=lambda *_: {"items": "0", "feed": []})
    assert prov.fetch_company_news("AAPL", since=date(2024, 1, 1)) == []


def test_alphavantage_rate_limit_body_raises() -> None:
    # No "feed" key: AV signals a bad key / rate limit with Information/Note. Must raise so the
    # assembler falls back to yfinance.
    body = {"Information": "rate limit reached; please try again"}
    prov = AlphaVantageNewsProvider("KEY", http_get_fn=lambda *_: body)
    with pytest.raises(ProviderError):
        prov.fetch_company_news("AAPL", since=date(2024, 1, 1))


def test_alphavantage_network_outage_raises() -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("network")

    prov = AlphaVantageNewsProvider("KEY", http_get_fn=boom, retries=1, sleep_fn=lambda _: None)
    with pytest.raises(ProviderError):
        prov.fetch_company_news("AAPL", since=date(2024, 1, 1))


def test_yfinance_news_fallback_flat_shape() -> None:
    rows = [
        {
            "title": "YF headline",
            "publisher": "Yahoo",
            "link": "https://example.com/yf",
            "providerPublishTime": 1735776000,
        },
        {  # before `since` -> filtered out
            "title": "old",
            "publisher": "Yahoo",
            "link": "https://example.com/old",
            "providerPublishTime": 1704067200,  # 2024-01-01
        },
    ]
    prov = YFinanceNewsProvider(news_fn=lambda _s: rows)
    items = prov.fetch_company_news("AAPL", since=date(2025, 1, 1))
    assert len(items) == 1
    assert items[0].headline == "YF headline"
    assert items[0].source == "Yahoo"
    assert isinstance(items[0].published_at, datetime)
