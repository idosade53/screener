"""F5 end-to-end dossier assembly against fakes (no network). Proves the cache-first flow: a cold
build fetches and persists; a warm rerun makes zero external calls (SC-2); a primary-provider
outage degrades to the fallback with a footer note (SC-4); an unknown symbol raises for a friendly
rejection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from screener.adapters.fundamentals.fmp_provider import FmpFundamentalsProvider
from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.domain.errors import ProviderError, UnknownSymbolError
from screener.domain.models import CompanyProfile, Dossier, FundamentalsSnapshot, NewsItem
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


def test_real_fmp_403_error_body_degrades_to_fallback(repo: SqliteScreenerRepository) -> None:
    # F7T02 end-to-end: the *real* FMP adapter, fed an {"Error Message": …} body (the free-tier
    # 403 shape), must raise ProviderError so the assembler serves fundamentals from yfinance.
    err = {"Error Message": "not available under your current subscription"}
    fmp = FmpFundamentalsProvider(
        "KEY",
        http_get_fn=lambda _u, _p, _t: err,
        retries=1,
        sleep_fn=lambda _s: None,
    )
    fallback = FakeFundamentalsProvider(source="fake-yf")
    fallback.seed(_profile("AAPL"), _snapshot("AAPL", source="fake-yf"))
    news = FakeNewsProvider()
    news.seed("AAPL", _news("AAPL"))
    svc = DossierService(
        repo=repo,
        fundamentals=fmp,
        fundamentals_fallback=fallback,
        news=news,
        clock=FrozenClock(_NOW),
    )

    dossier = svc.build("AAPL")
    assert dossier.snapshot.source == "fake-yf"
    assert any("fallback" in n for n in dossier.notes)


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


# --- F6: optional AI summary (SC-5) -----------------------------------------------
class _SpySummary:
    """Records how many times the AI stage is invoked; optionally raises to prove the guard."""

    def __init__(self, *, text: str = "AI read.", fail: bool = False) -> None:
        self._text = text
        self._fail = fail
        self.calls = 0

    def summarize(self, dossier: Dossier) -> str:
        self.calls += 1
        if self._fail:
            raise ProviderError("summary down")
        return self._text


def _ai_service(repo: SqliteScreenerRepository, summary: _SpySummary) -> DossierService:
    fund = FakeFundamentalsProvider()
    fund.seed(_profile("AAPL"), _snapshot("AAPL"))
    news = FakeNewsProvider()
    news.seed("AAPL", _news("AAPL"))
    return _service(repo, fund, news, summary=summary)


def test_ai_on_adds_exactly_one_call_and_populates_summary(repo: SqliteScreenerRepository) -> None:
    spy = _SpySummary(text="Net: constructive.")
    dossier = _ai_service(repo, spy).build("AAPL", with_ai=True)
    assert spy.calls == 1  # SC-5: on adds exactly one summarize stage
    assert dossier.ai_summary == "Net: constructive."
    assert "🤖 AI read" in format_dossier(dossier)


def test_ai_off_makes_zero_calls(repo: SqliteScreenerRepository) -> None:
    spy = _SpySummary()
    dossier = _ai_service(repo, spy).build("AAPL")  # default with_ai=False
    assert spy.calls == 0  # SC-5: off adds none
    assert dossier.ai_summary is None
    assert "🤖 AI read" not in format_dossier(dossier)


def test_ai_failure_degrades_to_note_not_crash(repo: SqliteScreenerRepository) -> None:
    spy = _SpySummary(fail=True)
    dossier = _ai_service(repo, spy).build("AAPL", with_ai=True)
    assert spy.calls == 1
    assert dossier.ai_summary is None  # still a valid dossier
    assert any("AI summary: unavailable" in n for n in dossier.notes)
