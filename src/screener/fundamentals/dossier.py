"""Dossier assembly (PRD FR-5). ``DossierService.build`` orchestrates the cache-first flow:

    cache check → providers (primary, fallback on outage) → scorecard → optional AI summary

It lives in the ``fundamentals`` core layer and talks to providers/repository only through their
ports, so it holds no adapter or SDK knowledge. Resilience (PRD §7): a provider outage degrades to
the fallback or a partial dossier with a footer note — it never crashes the caller. A genuinely
unknown symbol raises ``UnknownSymbolError`` for a friendly rejection (PRD §5).

Freshness (PRD §4.3): fundamentals stay fresh until the *later* of ``fetched_at +
fundamentals_cache_days`` or the ``next_earnings_date`` (nothing changes between earnings, so the
cache can safely outlive the base TTL); news uses a short ``news_cache_hours`` TTL. A warm cache hit
makes **zero** external calls (SC-2)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from screener.domain.errors import ProviderError, UnknownSymbolError
from screener.domain.models import (
    CachedFundamentals,
    Dossier,
    FundamentalsSnapshot,
    NewsCacheEntry,
    NewsItem,
)
from screener.fundamentals.scorecard import score
from screener.fundamentals.thresholds import ScorecardThresholds
from screener.ports.clock import Clock
from screener.ports.fundamentals import FundamentalsProvider
from screener.ports.news import NewsProvider
from screener.ports.repository import ScreenerRepository
from screener.ports.summary import SummaryProvider


@dataclass(frozen=True)
class DossierService:
    """Constructed once in the composition root with the selected providers; ``build`` is the
    single entry point the bot and CLI call."""

    repo: ScreenerRepository
    fundamentals: FundamentalsProvider
    news: NewsProvider
    clock: Clock
    thresholds: ScorecardThresholds = ScorecardThresholds.default()
    fundamentals_fallback: FundamentalsProvider | None = None
    news_fallback: NewsProvider | None = None
    summary: SummaryProvider | None = None
    fundamentals_cache_days: int = 1
    news_cache_hours: int = 6
    news_lookback_days: int = 7

    def build(
        self,
        symbol: str,
        *,
        force_refresh: bool = False,
        with_ai: bool = False,
        price: Decimal | None = None,
    ) -> Dossier:
        symbol = symbol.strip().upper()
        now = self.clock.now()
        notes: list[str] = []

        fund = self._load_fundamentals(symbol, now, force_refresh, notes)
        news_items = self._load_news(symbol, now, force_refresh, notes)

        card = score(fund.snapshot, self.thresholds, today=now.date(), price=price)
        dossier = Dossier(
            symbol=symbol,
            profile=fund.profile,
            snapshot=fund.snapshot,
            scorecard=card,
            news=news_items,
            generated_at=now,
            ai_summary=None,
            notes=tuple(notes),
        )
        if with_ai and self.summary is not None:
            dossier = replace(dossier, ai_summary=self._summarize(dossier, notes))
        return dossier

    # ------------------------------------------------------------ fundamentals
    def _load_fundamentals(
        self, symbol: str, now: datetime, force_refresh: bool, notes: list[str]
    ) -> CachedFundamentals:
        if not force_refresh:
            cached = self.repo.get_fundamentals_snapshot(symbol)
            if cached is not None and _fundamentals_fresh(
                cached.snapshot, now, self.fundamentals_cache_days
            ):
                return cached

        # Cold path: reject unknown symbols before spending fetch calls (PRD §5).
        if not self._known(symbol):
            raise UnknownSymbolError(symbol)

        try:
            fund = _fetch_fundamentals(self.fundamentals, symbol)
        except ProviderError:
            if self.fundamentals_fallback is None:
                raise
            notes.append("Fundamentals: primary provider unavailable — used fallback.")
            fund = _fetch_fundamentals(self.fundamentals_fallback, symbol)
        self.repo.put_fundamentals_snapshot(fund)
        return fund

    def _known(self, symbol: str) -> bool:
        if _safe_validate(self.fundamentals, symbol):
            return True
        return self.fundamentals_fallback is not None and _safe_validate(
            self.fundamentals_fallback, symbol
        )

    # -------------------------------------------------------------------- news
    def _load_news(
        self, symbol: str, now: datetime, force_refresh: bool, notes: list[str]
    ) -> tuple[NewsItem, ...]:
        if not force_refresh:
            cached = self.repo.get_news_cache(symbol)
            if cached is not None and _news_fresh(cached.fetched_at, now, self.news_cache_hours):
                return cached.items

        since = now.date() - timedelta(days=self.news_lookback_days)
        items, source = self._fetch_news(symbol, since, notes)
        self.repo.put_news_cache(
            NewsCacheEntry(symbol=symbol, fetched_at=now, source=source, items=items)
        )
        return items

    def _fetch_news(
        self, symbol: str, since: date, notes: list[str]
    ) -> tuple[tuple[NewsItem, ...], str]:
        try:
            return tuple(self.news.fetch_company_news(symbol, since)), _label(self.news)
        except ProviderError:
            if self.news_fallback is not None:
                try:
                    notes.append("News: primary provider unavailable — used fallback.")
                    return (
                        tuple(self.news_fallback.fetch_company_news(symbol, since)),
                        _label(self.news_fallback),
                    )
                except ProviderError:
                    pass
            notes.append("News: unavailable.")
            return (), "none"

    # --------------------------------------------------------------------- AI
    def _summarize(self, dossier: Dossier, notes: list[str]) -> str | None:
        assert self.summary is not None
        try:
            return self.summary.summarize(dossier)
        except Exception:  # noqa: BLE001 — the AI stage is best-effort; never fail the dossier
            notes.append("AI summary: unavailable.")
            return None


# ---------------------------------------------------------------------- helpers
def _fetch_fundamentals(provider: FundamentalsProvider, symbol: str) -> CachedFundamentals:
    return CachedFundamentals(
        profile=provider.fetch_profile(symbol),
        snapshot=provider.fetch_fundamentals(symbol),
    )


def _safe_validate(provider: FundamentalsProvider, symbol: str) -> bool:
    try:
        return provider.validate_symbol(symbol)
    except Exception:  # noqa: BLE001 — validation must not crash the request
        return False


def _label(provider: object) -> str:
    label = getattr(provider, "source", None)
    return str(label) if label else provider.__class__.__name__


def _fundamentals_fresh(snapshot: FundamentalsSnapshot, now: datetime, cache_days: int) -> bool:
    horizon = snapshot.fetched_at + timedelta(days=cache_days)
    if snapshot.next_earnings_date is not None:
        earnings_dt = datetime.combine(snapshot.next_earnings_date, time.min, tzinfo=UTC)
        if earnings_dt > horizon:
            horizon = earnings_dt
    return now < horizon


def _news_fresh(fetched_at: datetime, now: datetime, cache_hours: int) -> bool:
    return now < fetched_at + timedelta(hours=cache_hours)
