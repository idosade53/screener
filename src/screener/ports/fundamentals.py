"""Fundamentals access behind a port so the feed (FMP primary, yfinance fallback) can be
swapped or can fail without touching the scorecard, formatter, or bot (PRD §8, FR-1).

Contract obligations every implementation must satisfy (PRD §7 resilience, mirroring the
``market_data`` and ``notifier`` ports):
- Never raises past the adapter for a provider outage. Degrade to the fallback or return a
  partial snapshot with the missing metrics left ``None``; the caller surfaces a footer note.
- Normalises money/ratio values to ``Decimal`` (quantised to 4 dp) at the adapter boundary.
- Retries (3×, exponential backoff, jitter) live inside the adapter; the core never retries.
"""

from __future__ import annotations

from typing import Protocol

from screener.domain.models import CompanyProfile, FundamentalsSnapshot


class FundamentalsProvider(Protocol):
    def fetch_profile(self, symbol: str) -> CompanyProfile:
        """Header facts (PRD §4.1 row 1): name, sector/industry, market cap."""
        ...

    def fetch_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        """The derived, scored metrics (PRD §4.2). Absent metrics come back ``None``."""
        ...

    def validate_symbol(self, symbol: str) -> bool:
        """Used before a dossier is built to reject an unknown symbol (FR-1)."""
        ...
