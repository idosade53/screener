"""SQLite implementation of ``ScreenerRepository``. Schema is the PRD FR-6 DDL (which is also
the analytical-export target, §9.4) plus a derived ``indicator_cache`` table (§7.4).

Numeric policy: Decimal is persisted as TEXT and reconstructed on read (architecture §4). All
SQL is parameterised. ``save_scan`` is one transaction; ``try_claim_scan`` is an insert-or-ignore
on the scan id (idempotency, §8.4).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from screener.adapters.repository._fundamentals_codec import (
    dumps_cached_fundamentals,
    dumps_news_items,
    loads_cached_fundamentals,
    loads_news_items,
)
from screener.domain.errors import RepositoryError
from screener.domain.models import (
    Bar,
    CachedFundamentals,
    DeliveryStatus,
    Indicators,
    NewsCacheEntry,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    UniverseMember,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol            TEXT PRIMARY KEY,
    added_at          TEXT NOT NULL,
    active            INTEGER NOT NULL DEFAULT 1,
    last_validated_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   TEXT NOT NULL,
    high   TEXT NOT NULL,
    low    TEXT NOT NULL,
    close  TEXT NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS indicator_cache (
    symbol TEXT NOT NULL,
    asof   TEXT NOT NULL,
    sma150 TEXT NOT NULL,
    atr14  TEXT NOT NULL,
    PRIMARY KEY (symbol, asof)
);

CREATE TABLE IF NOT EXISTS scans (
    id              TEXT PRIMARY KEY,
    scan_type       TEXT NOT NULL,
    scheduled_at    TEXT NOT NULL,
    ran_at          TEXT NOT NULL,
    trading_day     TEXT NOT NULL,
    status          TEXT NOT NULL,
    symbols_scanned INTEGER NOT NULL,
    in_range_count  INTEGER NOT NULL,
    error_count     INTEGER NOT NULL,
    in_range_json   TEXT NOT NULL,
    error_json      TEXT NOT NULL,
    insufficient_json TEXT NOT NULL,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS scan_results (
    scan_id      TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    price        TEXT,
    sma150       TEXT,
    atr14        TEXT,
    distance_atr TEXT,
    in_range     INTEGER NOT NULL,
    status       TEXT NOT NULL,
    PRIMARY KEY (scan_id, symbol)
);

CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    message         TEXT NOT NULL,
    delivery_status TEXT NOT NULL
);

-- Idempotency markers, kept out of `scans` so latest_scan()/scans_on() only ever reflect
-- fully-persisted scans. A crash after claim but before save_scan blocks a duplicate re-run
-- without corrupting the diff baseline (§8.4).
CREATE TABLE IF NOT EXISTS scan_claims (
    scan_id    TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scans_ran_at ON scans (ran_at);
CREATE INDEX IF NOT EXISTS idx_scans_trading_day ON scans (trading_day);

-- Phase 4 cache (PRD §10, FR-6). Latest-only per symbol; payload_json is the derived scored
-- metrics (fundamentals) / the news items, tagged so Decimals/dates round-trip exactly.
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
    symbol             TEXT PRIMARY KEY,
    fetched_at         TEXT NOT NULL,
    next_earnings_date TEXT,
    payload_json       TEXT NOT NULL,
    source             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_cache (
    symbol       TEXT PRIMARY KEY,
    fetched_at   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source       TEXT NOT NULL
);
"""


def _json_list(values: Sequence[str]) -> str:
    return json.dumps(list(values))


def _load_list(raw: str) -> tuple[str, ...]:
    return tuple(json.loads(raw))


class SqliteScreenerRepository:
    """A single-file SQLite repository. Safe for the single-writer model the pipeline uses
    (reserved concurrency 1 / APScheduler max_instances=1)."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ universe
    def get_universe(self) -> list[UniverseMember]:
        rows = self._conn.execute(
            "SELECT symbol, added_at, active, last_validated_at FROM symbols WHERE active = 1"
            " ORDER BY symbol"
        ).fetchall()
        return [
            UniverseMember(
                symbol=r["symbol"],
                added_at=datetime.fromisoformat(r["added_at"]),
                active=bool(r["active"]),
                last_validated_at=(
                    datetime.fromisoformat(r["last_validated_at"])
                    if r["last_validated_at"]
                    else None
                ),
            )
            for r in rows
        ]

    def add_symbols(self, symbols: Sequence[str]) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._conn:
            for sym in symbols:
                # Re-activate on re-add; never duplicate (idempotent, §8.4).
                self._conn.execute(
                    "INSERT INTO symbols (symbol, added_at, active) VALUES (?, ?, 1)"
                    " ON CONFLICT(symbol) DO UPDATE SET active = 1",
                    (sym, now),
                )

    def remove_symbol(self, symbol: str) -> None:
        # Soft delete (ADR A12): flip active, keep observations.
        with self._conn:
            self._conn.execute(
                "UPDATE symbols SET active = 0 WHERE symbol = ?", (symbol,)
            )

    # ---------------------------------------------------------------------- bars
    def get_bars(self, symbol: str, since: date) -> list[Bar]:
        rows = self._conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily_bars"
            " WHERE symbol = ? AND date >= ? ORDER BY date",
            (symbol, since.isoformat()),
        ).fetchall()
        return [
            Bar(
                date=date.fromisoformat(r["date"]),
                open=Decimal(r["open"]),
                high=Decimal(r["high"]),
                low=Decimal(r["low"]),
                close=Decimal(r["close"]),
                volume=r["volume"],
            )
            for r in rows
        ]

    def upsert_bars(self, symbol: str, bars: Sequence[Bar]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO daily_bars (symbol, date, open, high, low, close, volume)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(symbol, date) DO UPDATE SET"
                " open=excluded.open, high=excluded.high, low=excluded.low,"
                " close=excluded.close, volume=excluded.volume",
                [
                    (
                        symbol,
                        b.date.isoformat(),
                        str(b.open),
                        str(b.high),
                        str(b.low),
                        str(b.close),
                        b.volume,
                    )
                    for b in bars
                ],
            )

    def latest_bar_date(self, symbol: str) -> date | None:
        row = self._conn.execute(
            "SELECT MAX(date) AS d FROM daily_bars WHERE symbol = ?", (symbol,)
        ).fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    def delete_bars(self, symbol: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM daily_bars WHERE symbol = ?", (symbol,))
            self._conn.execute("DELETE FROM indicator_cache WHERE symbol = ?", (symbol,))

    # ---------------------------------------------------------- indicator cache
    def get_indicators(
        self, symbols: Sequence[str], asof: date
    ) -> dict[str, Indicators]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        rows = self._conn.execute(
            f"SELECT symbol, asof, sma150, atr14 FROM indicator_cache"
            f" WHERE asof = ? AND symbol IN ({placeholders})",
            (asof.isoformat(), *symbols),
        ).fetchall()
        return {
            r["symbol"]: Indicators(
                sma150=Decimal(r["sma150"]),
                atr14=Decimal(r["atr14"]),
                asof=date.fromisoformat(r["asof"]),
            )
            for r in rows
        }

    def put_indicators(self, values: Mapping[str, Indicators]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO indicator_cache (symbol, asof, sma150, atr14)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(symbol, asof) DO UPDATE SET"
                " sma150=excluded.sma150, atr14=excluded.atr14",
                [
                    (sym, ind.asof.isoformat(), str(ind.sma150), str(ind.atr14))
                    for sym, ind in values.items()
                ],
            )

    # --------------------------------------------------------------------- scans
    def latest_scan(self) -> ScanSummary | None:
        row = self._conn.execute(
            "SELECT * FROM scans ORDER BY ran_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return self._summary_from_row(row) if row else None

    def scans_on(self, day: date) -> list[ScanSummary]:
        rows = self._conn.execute(
            "SELECT * FROM scans WHERE trading_day = ? ORDER BY ran_at",
            (day.isoformat(),),
        ).fetchall()
        return [self._summary_from_row(r) for r in rows]

    def save_scan(
        self, summary: ScanSummary, results: Sequence[SymbolScanResult]
    ) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO scans (id, scan_type, scheduled_at, ran_at, trading_day,"
                    " status, symbols_scanned, in_range_count, error_count, in_range_json,"
                    " error_json, insufficient_json, notes)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(id) DO UPDATE SET"
                    " status=excluded.status, ran_at=excluded.ran_at,"
                    " symbols_scanned=excluded.symbols_scanned,"
                    " in_range_count=excluded.in_range_count, error_count=excluded.error_count,"
                    " in_range_json=excluded.in_range_json, error_json=excluded.error_json,"
                    " insufficient_json=excluded.insufficient_json, notes=excluded.notes",
                    (
                        summary.scan_id,
                        summary.scan_type.value,
                        summary.scheduled_at.isoformat(),
                        summary.ran_at.isoformat(),
                        summary.trading_day.isoformat(),
                        summary.status.value,
                        summary.symbols_scanned,
                        len(summary.in_range),
                        len(summary.error_symbols),
                        _json_list(summary.in_range),
                        _json_list(summary.error_symbols),
                        _json_list(summary.insufficient_symbols),
                        summary.notes,
                    ),
                )
                self._conn.executemany(
                    "INSERT INTO scan_results (scan_id, symbol, price, sma150, atr14,"
                    " distance_atr, in_range, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(scan_id, symbol) DO UPDATE SET"
                    " price=excluded.price, sma150=excluded.sma150, atr14=excluded.atr14,"
                    " distance_atr=excluded.distance_atr, in_range=excluded.in_range,"
                    " status=excluded.status",
                    [
                        (
                            summary.scan_id,
                            r.symbol,
                            str(r.price) if r.price is not None else None,
                            str(r.indicators.sma150) if r.indicators else None,
                            str(r.indicators.atr14) if r.indicators else None,
                            str(r.distance_atr) if r.distance_atr is not None else None,
                            1 if r.in_range else 0,
                            r.status.value,
                        )
                        for r in results
                    ],
                )
        except sqlite3.Error as exc:  # repository write fails -> fail loudly (§8.3)
            raise RepositoryError(f"save_scan failed for {summary.scan_id}: {exc}") from exc

    def try_claim_scan(self, scan_id: str) -> bool:
        now = datetime.now().astimezone().isoformat()
        with self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO scan_claims (scan_id, claimed_at) VALUES (?, ?)",
                (scan_id, now),
            )
            return cur.rowcount == 1

    def _summary_from_row(self, row: sqlite3.Row) -> ScanSummary:
        return ScanSummary(
            scan_id=row["id"],
            scan_type=ScanType(row["scan_type"]),
            scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
            ran_at=datetime.fromisoformat(row["ran_at"]),
            trading_day=date.fromisoformat(row["trading_day"]),
            status=ScanStatus(row["status"]),
            symbols_scanned=row["symbols_scanned"],
            in_range=_load_list(row["in_range_json"]),
            error_symbols=_load_list(row["error_json"]),
            insufficient_symbols=_load_list(row["insufficient_json"]),
            notes=row["notes"],
        )

    # -------------------------------------------------------------------- alerts
    def record_alert(
        self, scan_id: str, message: str, status: DeliveryStatus
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO alerts (scan_id, sent_at, message, delivery_status)"
                " VALUES (?, ?, ?, ?)",
                (scan_id, now, message, status.value),
            )

    # ------------------------------------------------- fundamentals & news cache
    def get_fundamentals_snapshot(self, symbol: str) -> CachedFundamentals | None:
        row = self._conn.execute(
            "SELECT payload_json FROM fundamentals_snapshot WHERE symbol = ?", (symbol,)
        ).fetchone()
        return loads_cached_fundamentals(row["payload_json"]) if row else None

    def put_fundamentals_snapshot(self, cached: CachedFundamentals) -> None:
        snap = cached.snapshot
        with self._conn:
            self._conn.execute(
                "INSERT INTO fundamentals_snapshot"
                " (symbol, fetched_at, next_earnings_date, payload_json, source)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(symbol) DO UPDATE SET"
                " fetched_at=excluded.fetched_at, next_earnings_date=excluded.next_earnings_date,"
                " payload_json=excluded.payload_json, source=excluded.source",
                (
                    snap.symbol,
                    snap.fetched_at.isoformat(),
                    snap.next_earnings_date.isoformat() if snap.next_earnings_date else None,
                    dumps_cached_fundamentals(cached),
                    snap.source,
                ),
            )

    def get_news_cache(self, symbol: str) -> NewsCacheEntry | None:
        row = self._conn.execute(
            "SELECT fetched_at, payload_json, source FROM news_cache WHERE symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        return NewsCacheEntry(
            symbol=symbol,
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            source=row["source"],
            items=loads_news_items(row["payload_json"]),
        )

    def put_news_cache(self, entry: NewsCacheEntry) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO news_cache (symbol, fetched_at, payload_json, source)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(symbol) DO UPDATE SET"
                " fetched_at=excluded.fetched_at, payload_json=excluded.payload_json,"
                " source=excluded.source",
                (
                    entry.symbol,
                    entry.fetched_at.isoformat(),
                    dumps_news_items(entry.items),
                    entry.source,
                ),
            )
