"""Shared contract suite for ``ScreenerRepository`` (architecture §5). Any implementation —
the SQLite adapter now, DynamoDB later, and the in-memory fake used by integration tests — must
subclass this and provide a ``repo`` fixture. A fake that passes the same suite is a fake you can
trust."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from screener.domain.models import (
    Bar,
    CachedFundamentals,
    CompanyProfile,
    DeliveryStatus,
    FundamentalsSnapshot,
    Indicators,
    NewsCacheEntry,
    NewsItem,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    SymbolStatus,
)
from screener.ports.repository import ScreenerRepository


def _bar(d: str, close: str) -> Bar:
    return Bar(
        date=date.fromisoformat(d),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1000,
    )


def _summary(scan_id: str, day: str, in_range: tuple[str, ...], scan_type: ScanType) -> ScanSummary:
    ran = datetime.fromisoformat(f"{day}T20:15:00+00:00")
    return ScanSummary(
        scan_id=scan_id,
        scan_type=scan_type,
        scheduled_at=ran,
        ran_at=ran,
        trading_day=date.fromisoformat(day),
        status=ScanStatus.OK,
        symbols_scanned=len(in_range) + 1,
        in_range=in_range,
        error_symbols=(),
        insufficient_symbols=(),
    )


def _cached(symbol: str, **snap_overrides: object) -> CachedFundamentals:
    profile = CompanyProfile(
        symbol=symbol,
        name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        market_cap=Decimal("3200000000000"),
        currency="USD",
        exchange="NASDAQ",
    )
    defaults: dict[str, object] = {
        "symbol": symbol,
        "fetched_at": datetime.fromisoformat("2026-08-06T12:00:00+00:00"),
        "source": "fmp",
        "next_earnings_date": date(2026, 9, 1),
        "pe_ttm": Decimal("30.5"),
        "pe_fwd": None,
        "price_to_sales": None,
        "peg": Decimal("2.1"),
        "ev_ebitda": None,
        "price_to_book": None,
        "revenue_yoy": Decimal("0.0209"),
        "eps_yoy": None,
        "revenue_cagr_3y": None,
        "gross_margin": None,
        "operating_margin": None,
        "net_margin": Decimal("0.25"),
        "roe": None,
        "fcf_positive": True,
        "debt_to_equity": Decimal("1.8"),
        "current_ratio": Decimal("0.95"),
        "net_debt_to_ebitda": None,
        "interest_coverage": None,
        "analyst_rating": "Buy",
        "num_analysts": 34,
        "mean_target": Decimal("240"),
        "last_earnings_surprise_pct": None,
    }
    defaults.update(snap_overrides)
    return CachedFundamentals(profile=profile, snapshot=FundamentalsSnapshot(**defaults))  # type: ignore[arg-type]


def _news_entry(symbol: str, headlines: tuple[str, ...]) -> NewsCacheEntry:
    items = tuple(
        NewsItem(
            published_at=datetime.fromisoformat(f"2026-08-0{i + 1}T09:00:00+00:00"),
            source="Reuters",
            headline=h,
            url=f"https://example.com/{symbol}/{i}",
            summary=None if i % 2 else "s",
        )
        for i, h in enumerate(headlines)
    )
    return NewsCacheEntry(
        symbol=symbol,
        fetched_at=datetime.fromisoformat("2026-08-06T12:00:00+00:00"),
        source="finnhub",
        items=items,
    )


class RepositoryContract:
    """Subclasses must define a pytest fixture named ``repo`` returning a ScreenerRepository."""

    # ---- universe ----
    def test_add_and_get_universe_dedupes_and_uppercases_upstream(
        self, repo: ScreenerRepository
    ) -> None:
        repo.add_symbols(["AAPL", "KO"])
        repo.add_symbols(["AAPL"])  # idempotent re-add
        universe = {m.symbol for m in repo.get_universe()}
        assert universe == {"AAPL", "KO"}

    def test_remove_is_soft_delete(self, repo: ScreenerRepository) -> None:
        repo.add_symbols(["AAPL", "KO"])
        repo.remove_symbol("AAPL")
        assert {m.symbol for m in repo.get_universe()} == {"KO"}
        # Re-add reactivates rather than duplicating.
        repo.add_symbols(["AAPL"])
        assert {m.symbol for m in repo.get_universe()} == {"AAPL", "KO"}

    # ---- bars ----
    def test_upsert_and_get_bars(self, repo: ScreenerRepository) -> None:
        repo.upsert_bars("AAPL", [_bar("2026-08-05", "100"), _bar("2026-08-06", "101")])
        bars = repo.get_bars("AAPL", since=date(2026, 8, 1))
        assert [b.close for b in bars] == [Decimal("100"), Decimal("101")]
        assert repo.latest_bar_date("AAPL") == date(2026, 8, 6)

    def test_upsert_overwrites_on_conflict(self, repo: ScreenerRepository) -> None:
        repo.upsert_bars("AAPL", [_bar("2026-08-05", "100")])
        repo.upsert_bars("AAPL", [_bar("2026-08-05", "99")])  # correction
        bars = repo.get_bars("AAPL", since=date(2026, 8, 1))
        assert len(bars) == 1
        assert bars[0].close == Decimal("99")

    def test_get_bars_respects_since(self, repo: ScreenerRepository) -> None:
        repo.upsert_bars("AAPL", [_bar("2026-08-01", "1"), _bar("2026-08-10", "2")])
        bars = repo.get_bars("AAPL", since=date(2026, 8, 5))
        assert [b.date for b in bars] == [date(2026, 8, 10)]

    def test_delete_bars(self, repo: ScreenerRepository) -> None:
        repo.upsert_bars("AAPL", [_bar("2026-08-05", "100")])
        repo.delete_bars("AAPL")
        assert repo.get_bars("AAPL", since=date(2026, 1, 1)) == []
        assert repo.latest_bar_date("AAPL") is None

    def test_latest_bar_date_missing_symbol(self, repo: ScreenerRepository) -> None:
        assert repo.latest_bar_date("NOPE") is None

    # ---- indicator cache ----
    def test_put_and_get_indicators(self, repo: ScreenerRepository) -> None:
        asof = date(2026, 8, 6)
        repo.put_indicators(
            {"AAPL": Indicators(sma150=Decimal("196.9"), atr14=Decimal("3.1"), asof=asof)}
        )
        got = repo.get_indicators(["AAPL", "KO"], asof=asof)
        assert set(got) == {"AAPL"}
        assert got["AAPL"].sma150 == Decimal("196.9")

    def test_get_indicators_asof_mismatch_returns_empty(
        self, repo: ScreenerRepository
    ) -> None:
        repo.put_indicators(
            {"AAPL": Indicators(sma150=Decimal("1"), atr14=Decimal("1"), asof=date(2026, 8, 6))}
        )
        assert repo.get_indicators(["AAPL"], asof=date(2026, 8, 7)) == {}

    # ---- scans ----
    def test_save_and_latest_scan(self, repo: ScreenerRepository) -> None:
        summary = _summary("2026-08-06T20:15Z#CLOSE", "2026-08-06", ("AAPL",), ScanType.CLOSE)
        result = SymbolScanResult(
            symbol="AAPL",
            status=SymbolStatus.OK,
            price=Decimal("198.40"),
            indicators=Indicators(Decimal("196.9"), Decimal("3.1"), date(2026, 8, 6)),
            distance_atr=Decimal("0.48"),
            in_range=True,
        )
        repo.save_scan(summary, [result])
        latest = repo.latest_scan()
        assert latest is not None
        assert latest.scan_id == summary.scan_id
        assert latest.in_range == ("AAPL",)

    def test_latest_scan_orders_by_ran_at(self, repo: ScreenerRepository) -> None:
        older = _summary("2026-08-05T20:15Z#CLOSE", "2026-08-05", (), ScanType.CLOSE)
        newer = _summary("2026-08-06T20:15Z#CLOSE", "2026-08-06", ("KO",), ScanType.CLOSE)
        repo.save_scan(older, [])
        repo.save_scan(newer, [])
        latest = repo.latest_scan()
        assert latest is not None and latest.scan_id == newer.scan_id

    def test_scans_on_filters_by_trading_day(self, repo: ScreenerRepository) -> None:
        repo.save_scan(_summary("a#PRE", "2026-08-06", (), ScanType.PRE), [])
        repo.save_scan(_summary("b#OPEN", "2026-08-06", (), ScanType.OPEN), [])
        repo.save_scan(_summary("c#CLOSE", "2026-08-05", (), ScanType.CLOSE), [])
        assert len(repo.scans_on(date(2026, 8, 6))) == 2
        assert repo.scans_on(date(2026, 8, 4)) == []

    def test_latest_scan_none_when_empty(self, repo: ScreenerRepository) -> None:
        assert repo.latest_scan() is None

    # ---- idempotency ----
    def test_try_claim_scan_is_once_only(self, repo: ScreenerRepository) -> None:
        assert repo.try_claim_scan("2026-08-06T09:45Z#OPEN") is True
        assert repo.try_claim_scan("2026-08-06T09:45Z#OPEN") is False
        assert repo.try_claim_scan("2026-08-06T20:15Z#CLOSE") is True

    def test_claim_does_not_pollute_scan_history(self, repo: ScreenerRepository) -> None:
        # A claim must not appear as a scan — otherwise it corrupts the diff baseline (§8.4).
        repo.try_claim_scan("2026-08-06T09:45Z#OPEN")
        assert repo.latest_scan() is None
        assert repo.scans_on(date(2026, 8, 6)) == []

    # ---- alerts ----
    def test_record_alert(self, repo: ScreenerRepository) -> None:
        summary = _summary("s#CLOSE", "2026-08-06", (), ScanType.CLOSE)
        repo.save_scan(summary, [])
        # Should not raise.
        repo.record_alert(summary.scan_id, "hello", DeliveryStatus.SENT)

    # ---- fundamentals cache (Phase 4) ----
    def test_fundamentals_cache_miss_returns_none(self, repo: ScreenerRepository) -> None:
        assert repo.get_fundamentals_snapshot("AAPL") is None

    def test_fundamentals_cache_roundtrip_preserves_types(self, repo: ScreenerRepository) -> None:
        cached = _cached("AAPL")
        repo.put_fundamentals_snapshot(cached)
        got = repo.get_fundamentals_snapshot("AAPL")
        assert got is not None
        # Profile + snapshot both survive, and Decimal/date/datetime round-trip exactly.
        assert got.profile.name == "Apple Inc."
        assert got.profile.market_cap == Decimal("3200000000000")
        assert got.snapshot.pe_ttm == Decimal("30.5")
        assert got.snapshot.fcf_positive is True
        assert got.snapshot.next_earnings_date == date(2026, 9, 1)
        assert got.snapshot.fetched_at == cached.snapshot.fetched_at
        assert got.snapshot.price_to_sales is None

    def test_fundamentals_cache_is_latest_only(self, repo: ScreenerRepository) -> None:
        repo.put_fundamentals_snapshot(_cached("AAPL", pe_ttm=Decimal("10")))
        repo.put_fundamentals_snapshot(_cached("AAPL", pe_ttm=Decimal("20")))
        got = repo.get_fundamentals_snapshot("AAPL")
        assert got is not None and got.snapshot.pe_ttm == Decimal("20")

    def test_fundamentals_cache_null_earnings_date(self, repo: ScreenerRepository) -> None:
        repo.put_fundamentals_snapshot(_cached("KO", next_earnings_date=None))
        got = repo.get_fundamentals_snapshot("KO")
        assert got is not None and got.snapshot.next_earnings_date is None

    # ---- news cache (Phase 4) ----
    def test_news_cache_miss_returns_none(self, repo: ScreenerRepository) -> None:
        assert repo.get_news_cache("AAPL") is None

    def test_news_cache_roundtrip(self, repo: ScreenerRepository) -> None:
        entry = _news_entry("AAPL", ("first", "second"))
        repo.put_news_cache(entry)
        got = repo.get_news_cache("AAPL")
        assert got is not None
        assert got.symbol == "AAPL"
        assert got.source == "finnhub"
        assert got.fetched_at == entry.fetched_at
        assert [i.headline for i in got.items] == ["first", "second"]
        assert got.items[0].published_at == entry.items[0].published_at

    def test_news_cache_is_latest_only(self, repo: ScreenerRepository) -> None:
        repo.put_news_cache(_news_entry("AAPL", ("old",)))
        repo.put_news_cache(_news_entry("AAPL", ("new1", "new2")))
        got = repo.get_news_cache("AAPL")
        assert got is not None and [i.headline for i in got.items] == ["new1", "new2"]

    def test_news_cache_empty_items(self, repo: ScreenerRepository) -> None:
        repo.put_news_cache(_news_entry("KO", ()))
        got = repo.get_news_cache("KO")
        assert got is not None and got.items == ()
