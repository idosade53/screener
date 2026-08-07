"""Deliberately coarse-grained. Methods correspond to the access patterns in PRD §8.3 — not
to tables, and not to CRUD (ADR A3). A fine-grained repository would leak the relational model
into the core and make the DynamoDB implementation a hand-written ORM.

All database access goes through this interface (PRD §8.5). Flipping REPOSITORY_BACKEND from
``dynamodb`` to ``sqlite`` is the entire data-layer half of the RPi migration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from screener.domain.models import (
    Bar,
    DeliveryStatus,
    Indicators,
    ScanSummary,
    SymbolScanResult,
    UniverseMember,
)


class ScreenerRepository(Protocol):
    # ---- universe ----
    def get_universe(self) -> list[UniverseMember]: ...
    def add_symbols(self, symbols: Sequence[str]) -> None: ...
    def remove_symbol(self, symbol: str) -> None: ...  # soft delete: active=False (A12)

    # ---- bars ----
    def get_bars(self, symbol: str, since: date) -> list[Bar]: ...
    def upsert_bars(self, symbol: str, bars: Sequence[Bar]) -> None: ...
    def latest_bar_date(self, symbol: str) -> date | None: ...
    def delete_bars(self, symbol: str) -> None: ...  # split re-fetch

    # ---- indicator cache ----
    def get_indicators(
        self, symbols: Sequence[str], asof: date
    ) -> dict[str, Indicators]: ...
    def put_indicators(self, values: Mapping[str, Indicators]) -> None: ...

    # ---- scans ----
    def latest_scan(self) -> ScanSummary | None: ...
    def scans_on(self, day: date) -> list[ScanSummary]: ...
    def save_scan(
        self, summary: ScanSummary, results: Sequence[SymbolScanResult]
    ) -> None:
        """Writes the summary and all per-symbol observations as one logical operation
        (one transaction in SQLite; batched writes + a final summary put in DynamoDB)."""
        ...

    def try_claim_scan(self, scan_id: str) -> bool:
        """Idempotency: returns True if this invocation claimed the scan, False if it was
        already claimed (duplicate invocation)."""
        ...

    # ---- alerts ----
    def record_alert(
        self, scan_id: str, message: str, status: DeliveryStatus
    ) -> None: ...
