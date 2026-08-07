"""Full-pipeline integration tests: real SQLite repository + fake provider/calendar/clock/
notifier, recorded bars, frozen clock, no network. Exercises the change-detection sequence
(FR-4/Q1) and the failure taxonomy (§8.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.config import Settings, load_settings
from screener.domain.models import ScanStatus, ScanType
from screener.screener.criterion import MA150ProximityCriterion
from screener.screener.pipeline import ScanPipeline
from tests.integration.fakes import (
    CollectingNotifier,
    FakeCalendar,
    FakeProvider,
    FrozenClock,
    make_bars,
)

# 170 flat bars at 100 -> SMA150 = 100, ATR14 = 2 (H-L range of 2), band = 1.5*2 = 3.
# In-range window is therefore [97, 103] for a symbol with this history.
_HISTORY_START = date(2026, 2, 1)
_FLAT = ["100"] * 170


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(db_path=str(tmp_path / "screener.db"))


@pytest.fixture
def repo(settings: Settings) -> SqliteScreenerRepository:
    return SqliteScreenerRepository(settings.db_path)


@pytest.fixture
def notifier() -> CollectingNotifier:
    return CollectingNotifier()


def _pipeline(
    repo: SqliteScreenerRepository,
    provider: FakeProvider,
    notifier: CollectingNotifier,
    now: datetime,
    settings: Settings,
) -> ScanPipeline:
    return ScanPipeline(
        repo=repo,
        provider=provider,
        calendar=FakeCalendar(),
        clock=FrozenClock(now),
        notifier=notifier,
        criteria=[MA150ProximityCriterion(settings.band_atr_mult)],
        settings=settings,
    )


def _seed_repo_history(repo: SqliteScreenerRepository, symbols: list[str], last: date) -> None:
    """Seed flat history directly into the repo, ending on `last` (the indicator_asof for
    intraday scans)."""
    # make_bars advances one calendar day per bar; choose a start so the run ends on `last`.
    from datetime import timedelta

    start = last - timedelta(days=len(_FLAT) - 1)
    for sym in symbols:
        repo.upsert_bars(sym, make_bars(start, _FLAT))


def test_scripted_sequence_baseline_suppression_and_manual(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL", "KO"])
    # Intraday scans on Friday 2026-08-07 read indicators as-of Thursday 2026-08-06.
    _seed_repo_history(repo, ["AAPL", "KO"], last=date(2026, 8, 6))
    provider = FakeProvider()
    # The MANUAL scan refreshes bars, so the provider must also serve history through 08-06.
    from datetime import timedelta

    hist_start = date(2026, 8, 6) - timedelta(days=len(_FLAT) - 1)
    for sym in ("AAPL", "KO"):
        provider.seed_bars(sym, make_bars(hist_start, _FLAT))

    # 1) PRE 09:00 ET (13:00 UTC), first of day: AAPL in band (100), KO out (110).
    provider.quotes = {"AAPL": Decimal("100"), "KO": Decimal("110")}
    pre = _pipeline(repo, provider, notifier, datetime(2026, 8, 7, 13, 0, tzinfo=UTC), settings)
    pre.run(ScanType.PRE)

    # 2) OPEN 09:45 ET, same in-range set -> suppressed (not first, not manual, unchanged).
    open_ = _pipeline(repo, provider, notifier, datetime(2026, 8, 7, 13, 45, tzinfo=UTC), settings)
    open_.run(ScanType.OPEN)

    # 3) MANUAL 11:00 ET, KO now enters the band (101) -> manual always sends.
    provider.quotes = {"AAPL": Decimal("100"), "KO": Decimal("101")}
    man = _pipeline(repo, provider, notifier, datetime(2026, 8, 7, 15, 0, tzinfo=UTC), settings)
    man.run(ScanType.MANUAL)

    # Three scans persisted.
    assert len(repo.scans_on(date(2026, 8, 7))) == 3
    # Only PRE (baseline) and MANUAL sent; OPEN suppressed.
    assert len(notifier.messages) == 2
    assert "PRE scan" in notifier.messages[0]
    assert "Entered: KO" in notifier.messages[1]

    latest = repo.latest_scan()
    assert latest is not None
    assert set(latest.in_range) == {"AAPL", "KO"}


def test_close_computes_and_caches_indicators_and_uses_official_close(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL"])
    provider = FakeProvider()
    # Provider serves bars through the CLOSE trading_day (Friday 2026-08-07).
    from datetime import timedelta

    start = date(2026, 8, 7) - timedelta(days=len(_FLAT) - 1)
    provider.seed_bars("AAPL", make_bars(start, _FLAT))

    # CLOSE 20:15 ET Friday == 2026-08-08 00:15 UTC.
    close = _pipeline(
        repo, provider, notifier, datetime(2026, 8, 8, 0, 15, tzinfo=UTC), settings
    )
    summary = close.run(ScanType.CLOSE)

    assert summary.status is ScanStatus.OK
    assert summary.in_range == ("AAPL",)  # P = official close 100 == SMA150
    # Indicator cache populated for the CLOSE asof (Friday), so PRE/OPEN can reuse it.
    cached = repo.get_indicators(["AAPL"], asof=date(2026, 8, 7))
    assert cached["AAPL"].sma150 == Decimal("100.0000")
    assert cached["AAPL"].atr14 == Decimal("2.0000")


def test_per_symbol_failure_does_not_empty_the_set(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL", "GHOST"])
    provider = FakeProvider()
    from datetime import timedelta

    start = date(2026, 8, 7) - timedelta(days=len(_FLAT) - 1)
    provider.seed_bars("AAPL", make_bars(start, _FLAT))
    provider.bar_failures["GHOST"] = "no data"  # GHOST never fetches

    close = _pipeline(
        repo, provider, notifier, datetime(2026, 8, 8, 0, 15, tzinfo=UTC), settings
    )
    summary = close.run(ScanType.CLOSE)

    # AAPL still in range; GHOST is a DATA_ERROR, excluded, and must not blank the set.
    assert summary.in_range == ("AAPL",)
    assert "GHOST" in summary.error_symbols
    assert "No data: GHOST" in notifier.messages[-1]


def test_staleness_guard_aborts_and_does_not_persist(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL", "KO"])
    provider = FakeProvider()
    # Provider only has bars two sessions stale (through 2026-08-05), for a CLOSE expecting
    # 2026-08-07 -> newest bar older than expected -> abort (§7.2).
    from datetime import timedelta

    stale_end = date(2026, 8, 5)
    start = stale_end - timedelta(days=len(_FLAT) - 1)
    for sym in ("AAPL", "KO"):
        provider.seed_bars(sym, make_bars(start, _FLAT))

    close = _pipeline(
        repo, provider, notifier, datetime(2026, 8, 8, 0, 15, tzinfo=UTC), settings
    )
    summary = close.run(ScanType.CLOSE)

    assert summary.status is ScanStatus.ABORTED
    assert repo.latest_scan() is None  # not persisted -> diff baseline stays clean
    assert any("SYSTEM" in m for m in notifier.messages)


def test_insufficient_history_is_reported_not_in_range(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL"])
    provider = FakeProvider()
    from datetime import timedelta

    # Only 10 bars -> below MIN_BARS_REQUIRED (165).
    short = ["100"] * 10
    start = date(2026, 8, 7) - timedelta(days=len(short) - 1)
    provider.seed_bars("AAPL", make_bars(start, short))

    close = _pipeline(
        repo, provider, notifier, datetime(2026, 8, 8, 0, 15, tzinfo=UTC), settings
    )
    summary = close.run(ScanType.CLOSE)

    assert summary.in_range == ()
    assert "AAPL" in summary.insufficient_symbols
    # Reported in the footer on every message (Q3).
    assert "Insufficient history: AAPL" in notifier.messages[-1]


def test_missed_earlier_scan_is_reported(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL"])
    _seed_repo_history(repo, ["AAPL"], last=date(2026, 8, 6))
    provider = FakeProvider()
    provider.quotes = {"AAPL": Decimal("100")}

    # OPEN 09:45 ET runs with no PRE (09:00) recorded for the day -> gap check fires.
    open_ = _pipeline(repo, provider, notifier, datetime(2026, 8, 7, 13, 45, tzinfo=UTC), settings)
    open_.run(ScanType.OPEN)

    assert any("SYSTEM missed PRE" in m for m in notifier.messages)


def test_missed_scan_not_reported_twice_across_the_day(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL"])
    _seed_repo_history(repo, ["AAPL"], last=date(2026, 8, 6))
    provider = FakeProvider()
    provider.quotes = {"AAPL": Decimal("100")}

    # OPEN reports the missing PRE...
    _pipeline(repo, provider, notifier, datetime(2026, 8, 7, 13, 45, tzinfo=UTC), settings).run(
        ScanType.OPEN
    )
    # ...and the later CLOSE (which serves official closes) must not re-report it.
    close = _pipeline(repo, provider, notifier, datetime(2026, 8, 8, 0, 15, tzinfo=UTC), settings)
    from datetime import timedelta

    start = date(2026, 8, 7) - timedelta(days=len(_FLAT) - 1)
    provider.seed_bars("AAPL", make_bars(start, _FLAT))
    close.run(ScanType.CLOSE)

    assert sum("SYSTEM missed PRE" in m for m in notifier.messages) == 1


def test_idempotent_claim_prevents_duplicate_run(
    repo: SqliteScreenerRepository, notifier: CollectingNotifier, settings: Settings
) -> None:
    repo.add_symbols(["AAPL"])
    provider = FakeProvider()
    from datetime import timedelta

    start = date(2026, 8, 7) - timedelta(days=len(_FLAT) - 1)
    provider.seed_bars("AAPL", make_bars(start, _FLAT))
    now = datetime(2026, 8, 8, 0, 15, tzinfo=UTC)

    first = _pipeline(repo, provider, notifier, now, settings).run(ScanType.CLOSE)
    # Same scheduled time -> same deterministic scan_id -> second run is a no-op.
    second = _pipeline(repo, provider, notifier, now, settings).run(ScanType.CLOSE)

    assert first.status is ScanStatus.OK
    assert second.status is ScanStatus.SKIPPED
    assert len(repo.scans_on(date(2026, 8, 7))) == 1
