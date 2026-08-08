"""M5 bot command/dispatch tests. Pure logic — no network: a real in-memory SQLite repo, a
canned scan callable, and directly-constructed BotUpdates. Covers auth, every command, the
universe cap soft-guard, invalid-symbol reporting, and add/remove idempotency."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.bot.context import BotContext
from screener.bot.dispatch import dispatch
from screener.bot.models import BotUpdate
from screener.config import Settings, load_settings
from screener.domain.errors import UnknownSymbolError
from screener.domain.models import (
    CompanyProfile,
    Dossier,
    FundamentalsSnapshot,
    ScanStatus,
    ScanSummary,
    ScanType,
    Scorecard,
)

CHAT = "123456"


def _fake_dossier(symbol: str) -> Dossier:
    if symbol == "NOPE":
        raise UnknownSymbolError(symbol)
    now = datetime(2026, 8, 7, 20, 15, tzinfo=UTC)
    profile = CompanyProfile(
        symbol=symbol,
        name=f"{symbol} Inc.",
        sector="Technology",
        industry="Software",
        market_cap=None,
        currency="USD",
        exchange="NASDAQ",
    )
    snapshot = FundamentalsSnapshot(
        symbol=symbol,
        fetched_at=now,
        source="fake",
        next_earnings_date=None,
        pe_ttm=None,
        pe_fwd=None,
        price_to_sales=None,
        peg=None,
        ev_ebitda=None,
        price_to_book=None,
        revenue_yoy=None,
        eps_yoy=None,
        revenue_cagr_3y=None,
        gross_margin=None,
        operating_margin=None,
        net_margin=None,
        roe=None,
        fcf_positive=None,
        debt_to_equity=None,
        current_ratio=None,
        net_debt_to_ebitda=None,
        interest_coverage=None,
        analyst_rating=None,
        num_analysts=None,
        mean_target=None,
        last_earnings_surprise_pct=None,
    )
    return Dossier(
        symbol=symbol,
        profile=profile,
        snapshot=snapshot,
        scorecard=Scorecard(lines=()),
        news=(),
        generated_at=now,
    )


def _summary() -> ScanSummary:
    now = datetime(2026, 8, 7, 20, 15, tzinfo=UTC)
    return ScanSummary(
        scan_id="2026-08-07T20:15Z#MANUAL",
        scan_type=ScanType.MANUAL,
        scheduled_at=now,
        ran_at=now,
        trading_day=date(2026, 8, 7),
        status=ScanStatus.OK,
        symbols_scanned=2,
        in_range=("AAPL",),
        error_symbols=(),
        insufficient_symbols=(),
    )


class _Scanner:
    """Stand-in for the MANUAL-scan callable; records that it was invoked."""

    def __init__(self, summary: ScanSummary) -> None:
        self.summary = summary
        self.calls = 0

    def __call__(self) -> ScanSummary:
        self.calls += 1
        return self.summary


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(
        db_path=str(tmp_path / "screener.db"), telegram_chat_id=CHAT, universe_cap=5
    )


@pytest.fixture
def repo(settings: Settings) -> SqliteScreenerRepository:
    return SqliteScreenerRepository(settings.db_path)


@pytest.fixture
def scanner() -> _Scanner:
    return _Scanner(_summary())


@pytest.fixture
def ctx(
    repo: SqliteScreenerRepository, settings: Settings, scanner: _Scanner
) -> BotContext:
    return BotContext(
        repo=repo, settings=settings, run_scan=scanner, build_dossier=_fake_dossier
    )


def _update(text: str, chat: str = CHAT) -> BotUpdate:
    return BotUpdate(update_id=1, chat_id=chat, text=text)


def _symbols(repo: SqliteScreenerRepository) -> set[str]:
    return {m.symbol for m in repo.get_universe()}


# ---- auth -----------------------------------------------------------------

def test_unauthorized_chat_is_ignored_silently(ctx: BotContext) -> None:
    assert dispatch(_update("/list", chat="999"), ctx) is None


def test_authorized_chat_gets_a_reply(ctx: BotContext) -> None:
    assert dispatch(_update("/list"), ctx) is not None


# ---- help / unknown / free text -------------------------------------------

def test_help_lists_commands(ctx: BotContext) -> None:
    reply = dispatch(_update("/help"), ctx)
    assert reply is not None and "/add" in reply and "/scan" in reply


def test_unknown_command_falls_back_to_help(ctx: BotContext) -> None:
    reply = dispatch(_update("/frobnicate"), ctx)
    assert reply is not None and "Unknown command" in reply and "/add" in reply


def test_non_command_text_returns_help(ctx: BotContext) -> None:
    reply = dispatch(_update("hello there"), ctx)
    assert reply is not None and "/help" in reply


def test_botname_suffix_is_stripped(ctx: BotContext) -> None:
    reply = dispatch(_update("/list@screener_bot"), ctx)
    assert reply == "Universe is empty."


# ---- add ------------------------------------------------------------------

def test_add_persists_and_reports(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    reply = dispatch(_update("/add aapl msft"), ctx)
    assert reply is not None and "Added" in reply
    assert _symbols(repo) == {"AAPL", "MSFT"}


def test_add_reports_invalid_symbols(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    reply = dispatch(_update("/add aapl 1bad"), ctx)
    assert reply is not None and "Invalid" in reply and "1bad" in reply
    assert _symbols(repo) == {"AAPL"}


def test_add_is_idempotent(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    dispatch(_update("/add aapl"), ctx)
    reply = dispatch(_update("/add aapl"), ctx)
    assert reply is not None and "Already present" in reply
    assert _symbols(repo) == {"AAPL"}


def test_add_respects_universe_cap(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    # universe_cap is 5 in the settings fixture.
    reply = dispatch(_update("/add a b c d e f"), ctx)
    assert reply is not None and "cap 5" in reply
    assert len(_symbols(repo)) == 5


def test_add_without_args_shows_usage(ctx: BotContext) -> None:
    reply = dispatch(_update("/add"), ctx)
    assert reply is not None and "Usage" in reply


# ---- remove ---------------------------------------------------------------

def test_remove_soft_deletes(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    dispatch(_update("/add aapl msft"), ctx)
    reply = dispatch(_update("/remove aapl"), ctx)
    assert reply is not None and "Removed" in reply
    assert _symbols(repo) == {"MSFT"}


def test_remove_absent_symbol_is_noop(ctx: BotContext, repo: SqliteScreenerRepository) -> None:
    reply = dispatch(_update("/remove zzzz"), ctx)
    assert reply is not None and "Not in universe" in reply
    assert _symbols(repo) == set()


# ---- list -----------------------------------------------------------------

def test_list_empty_then_populated(ctx: BotContext) -> None:
    assert dispatch(_update("/list"), ctx) == "Universe is empty."
    dispatch(_update("/add aapl msft"), ctx)
    reply = dispatch(_update("/list"), ctx)
    assert reply is not None and "AAPL" in reply and "2 symbols" in reply


# ---- status ---------------------------------------------------------------

def test_status_no_scans(ctx: BotContext) -> None:
    reply = dispatch(_update("/status"), ctx)
    assert reply is not None and "No scans" in reply


def test_status_reports_latest_scan(
    ctx: BotContext, repo: SqliteScreenerRepository
) -> None:
    repo.save_scan(_summary(), [])
    reply = dispatch(_update("/status"), ctx)
    assert reply is not None and "MANUAL" in reply and "In range: 1" in reply


# ---- scan -----------------------------------------------------------------

def test_scan_triggers_a_manual_run(ctx: BotContext, scanner: _Scanner) -> None:
    reply = dispatch(_update("/scan"), ctx)
    assert scanner.calls == 1
    assert reply is not None and "Scan complete" in reply and "1 in range" in reply


# ---- dossier --------------------------------------------------------------

def test_dossier_returns_report(ctx: BotContext) -> None:
    reply = dispatch(_update("/dossier aapl"), ctx)
    assert reply is not None and "AAPL — AAPL Inc." in reply and "Scorecard" in reply


def test_dossier_alias_dd(ctx: BotContext) -> None:
    reply = dispatch(_update("/dd aapl"), ctx)
    assert reply is not None and "AAPL Inc." in reply


def test_dossier_unknown_symbol_is_friendly(ctx: BotContext) -> None:
    reply = dispatch(_update("/dossier nope"), ctx)
    assert reply is not None and "Unknown symbol" in reply


def test_dossier_without_args_shows_usage(ctx: BotContext) -> None:
    reply = dispatch(_update("/dossier"), ctx)
    assert reply is not None and "Usage" in reply


def test_dossier_from_unauthorized_chat_is_ignored(ctx: BotContext) -> None:
    assert dispatch(_update("/dossier aapl", chat="999"), ctx) is None
