"""F5 end-to-end dossier assembly against fakes (no network). Proves the cache-first flow: a cold
build fetches and persists; a warm rerun makes zero external calls (SC-2); a primary-provider
outage degrades to the fallback with a footer note (SC-4); an unknown symbol raises for a friendly
rejection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.domain.errors import UnknownSymbolError
from screener.domain.models import CompanyProfile, FundamentalsSnapshot, NewsItem
from screener.fundamentals.dossier import DossierService
from screener.fundamentals.formatters import format_dossier
from tests.integration.fakes import FakeFundamentalsProvider, FakeNewsProvider, FrozenClock

_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _profile(symbol: str) -> CompanyProfile:
    return CompanyProfile(
        symbol=symbol,
        name=f"{symbol} Inc.",
        sector="Technology",
        industry="Software",
        market_cap=Decimal("3200000000000"),
        currency="USD",
        exchange="NASDAQ",
    )


def _snapshot(symbol: str, *, source: str = "fake-fmp") -> FundamentalsSnapshot:
    return FundamentalsSnapshot(
        symbol=symbol,
        fetched_at=_NOW,
        source=source,
        next_earnings_date=None,
        pe_ttm=Decimal("18"),
        pe_fwd=None,
        price_to_sales=None,
        peg=Decimal("1.2"),
        ev_ebitda=None,
        price_to_book=None,
        revenue_yoy=Decimal("0.20"),
        eps_yoy=None,
        revenue_cagr_3y=None,
        gross_margin=None,
        operating_margin=None,
        net_margin=Decimal("0.25"),
        roe=None,
        fcf_positive=True,
        debt_to_equity=Decimal("0.3"),
        current_ratio=Decimal("2.0"),
        net_debt_to_ebitda=None,
        interest_coverage=None,
        analyst_rating="Buy",
        num_analysts=30,
        mean_target=Decimal("240"),
        last_earnings_surprise_pct=None,
    )


def _news(symbol: str) -> list[NewsItem]:
    return [
        NewsItem(
            published_at=datetime(2026, 8, 9, 14, tzinfo=UTC),
            source="Reuters",
            headline=f"{symbol} beats estimates",
            url="https://example.com/1",
            summary=None,
        )
    ]


@pytest.fixture
def repo(tmp_path: Path) -> SqliteScreenerRepository:
    return SqliteScreenerRepository(str(tmp_path / "screener.db"))


def _service(
    repo: SqliteScreenerRepository,
    fundamentals: FakeFundamentalsProvider,
    news: FakeNewsProvider,
    **overrides: object,
) -> DossierService:
    return DossierService(
        repo=repo,
        fundamentals=fundamentals,
        news=news,
        clock=FrozenClock(_NOW),
        **overrides,  # type: ignore[arg-type]
    )


def test_cold_build_renders_and_persists(repo: SqliteScreenerRepository) -> None:
    fund = FakeFundamentalsProvider()
    fund.seed(_profile("AAPL"), _snapshot("AAPL"))
    news = FakeNewsProvider()
    news.seed("AAPL", _news("AAPL"))
    svc = _service(repo, fund, news)

    dossier = svc.build("aapl")
    assert dossier.symbol == "AAPL"
    assert dossier.scorecard.tally  # non-empty — the snapshot scored
    assert dossier.news[0].headline == "AAPL beats estimates"
    rendered = format_dossier(dossier)
    assert "AAPL Inc." in rendered and "Scorecard" in rendered
    # Persisted for the next call.
    assert repo.get_fundamentals_snapshot("AAPL") is not None
    assert repo.get_news_cache("AAPL") is not None


def test_warm_cache_hit_makes_zero_external_calls(repo: SqliteScreenerRepository) -> None:
    fund = FakeFundamentalsProvider()
    fund.seed(_profile("AAPL"), _snapshot("AAPL"))
    news = FakeNewsProvider()
    news.seed("AAPL", _news("AAPL"))
    svc = _service(repo, fund, news)

    svc.build("AAPL")  # cold: populates cache
    calls_after_cold = (fund.calls, news.calls)
    assert calls_after_cold[0] > 0 and calls_after_cold[1] > 0

    svc.build("AAPL")  # warm: must not touch providers (SC-2)
    assert (fund.calls, news.calls) == calls_after_cold


def test_force_refresh_bypasses_cache(repo: SqliteScreenerRepository) -> None:
    fund = FakeFundamentalsProvider()
    fund.seed(_profile("AAPL"), _snapshot("AAPL"))
    news = FakeNewsProvider()
    news.seed("AAPL", _news("AAPL"))
    svc = _service(repo, fund, news)

    svc.build("AAPL")
    before = fund.calls
    svc.build("AAPL", force_refresh=True)
    assert fund.calls > before


def test_primary_outage_degrades_to_fallback(repo: SqliteScreenerRepository) -> None:
    primary = FakeFundamentalsProvider(fail=True)
    fallback = FakeFundamentalsProvider(source="fake-yf")
    fallback.seed(_profile("AAPL"), _snapshot("AAPL", source="fake-yf"))
    news_primary = FakeNewsProvider(fail=True)
    news_fallback = FakeNewsProvider()
    news_fallback.seed("AAPL", _news("AAPL"))
    svc = _service(
        repo,
        primary,
        news_primary,
        fundamentals_fallback=fallback,
        news_fallback=news_fallback,
    )

    dossier = svc.build("AAPL")
    assert dossier.snapshot.source == "fake-yf"
    assert dossier.news[0].headline == "AAPL beats estimates"
    notes = " ".join(dossier.notes)
    assert "fallback" in notes  # both footer notes mention the fallback


def test_double_news_outage_is_partial_not_fatal(repo: SqliteScreenerRepository) -> None:
    fund = FakeFundamentalsProvider()
    fund.seed(_profile("AAPL"), _snapshot("AAPL"))
    svc = _service(
        repo,
        fund,
        FakeNewsProvider(fail=True),
        news_fallback=FakeNewsProvider(fail=True),
    )
    dossier = svc.build("AAPL")
    assert dossier.news == ()
    assert any("News: unavailable" in n for n in dossier.notes)


def test_unknown_symbol_raises(repo: SqliteScreenerRepository) -> None:
    svc = _service(repo, FakeFundamentalsProvider(), FakeNewsProvider())
    with pytest.raises(UnknownSymbolError):
        svc.build("NOPE")
