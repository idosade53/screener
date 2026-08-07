"""Core domain types. These cross module boundaries; adapters translate to and from
their own storage or wire formats at the edge.

Numeric policy (architecture §4, ADR A11): prices, MAs and ATRs are ``Decimal`` at every
boundary. ``float`` exists only inside ``indicators/`` where pandas requires it, and is
converted back — quantised to 4 dp — on the way out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class ScanType(StrEnum):
    PRE = "PRE"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    MANUAL = "MANUAL"


class PriceMode(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    OFFICIAL_CLOSE = "OFFICIAL_CLOSE"


class SymbolStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # < MIN_BARS_REQUIRED completed bars
    DATA_ERROR = "DATA_ERROR"  # fetch failed after retries
    STALE_PRICE = "STALE_PRICE"  # fell back to previous close (Q2)


class ScanStatus(StrEnum):
    OK = "OK"
    ABORTED = "ABORTED"  # staleness guard or >50% universe failure (§7.2)
    SKIPPED = "SKIPPED"  # not a trading day, empty universe, already claimed


class DeliveryStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"  # no change -> intentionally not sent


@dataclass(frozen=True)
class Bar:
    """One completed daily OHLCV bar (unadjusted, per FR-2)."""

    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    added_at: datetime
    active: bool
    last_validated_at: datetime | None


@dataclass(frozen=True)
class Indicators:
    sma150: Decimal
    atr14: Decimal
    asof: date  # last completed bar feeding SMA/ATR; MUST equal ScanContext.indicator_asof


@dataclass(frozen=True)
class ScanContext:
    """Everything the pipeline needs to know about *when* it is running. Constructed once,
    at the top of a scan, from (ScanType, Clock, TradingCalendar). No stage below this ever
    calls the clock again (ADR A4)."""

    scan_type: ScanType
    scan_id: str  # e.g. "2026-08-07T09:45Z#OPEN" — idempotency key
    ran_at: datetime  # UTC, tz-aware
    trading_day: date  # the ET session this scan belongs to
    indicator_asof: date  # last completed bar feeding SMA/ATR (§4.3)
    price_mode: PriceMode
    is_first_of_day: bool  # drives the unconditional baseline (Q1)


@dataclass(frozen=True)
class SymbolScanResult:
    symbol: str
    status: SymbolStatus
    price: Decimal | None
    indicators: Indicators | None
    distance_atr: Decimal | None  # (P - SMA150) / ATR14, signed
    in_range: bool  # always False for any non-OK status

    def __post_init__(self) -> None:
        # A failed/insufficient symbol can never appear in the in-range set — this is what
        # stops a data outage from generating a spurious "Exited: everything" message
        # (architecture §4). STALE_PRICE is *not* an outage: it carries a real fallback price
        # (the previous close, Q2), so it remains evaluable and may be in-range, shown with a
        # `~` prefix (FR-5). [Reconciliation of architecture §4 "any non-OK" against FR-5's
        # `~`-prefixed in-range rows and the purpose of the PRE scan.]
        if self.in_range and self.status in (
            SymbolStatus.DATA_ERROR,
            SymbolStatus.INSUFFICIENT_DATA,
        ):
            raise ValueError(f"{self.symbol}: in_range must be False when status is {self.status}")


@dataclass(frozen=True)
class ScanSummary:
    scan_id: str
    scan_type: ScanType
    scheduled_at: datetime
    ran_at: datetime
    trading_day: date
    status: ScanStatus
    symbols_scanned: int
    in_range: tuple[str, ...]  # sorted symbols with in_range == True, stored inline (§8.3)
    error_symbols: tuple[str, ...]  # DATA_ERROR
    insufficient_symbols: tuple[str, ...]  # INSUFFICIENT_DATA
    notes: str | None = None


@dataclass(frozen=True)
class Diff:
    """Result of comparing the current in-range set with the previous scan's set (FR-4)."""

    current: frozenset[str]
    previous: frozenset[str]
    should_send: bool  # MANUAL ∨ is_first_of_day ∨ sets differ

    @property
    def entered(self) -> frozenset[str]:
        return self.current - self.previous

    @property
    def exited(self) -> frozenset[str]:
        return self.previous - self.current

    @property
    def changed(self) -> bool:
        return self.current != self.previous
