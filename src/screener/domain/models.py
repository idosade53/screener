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


# --- Phase 4: Fundamentals & News Dossier (PRD-fundamentals-dossier) ---------------------
#
# Cache-first, on-demand per-symbol dossier. Same numeric policy as above: all money/ratio
# values are ``Decimal`` (quantised to 4 dp by the provider adapter at its boundary, F3), and
# any metric a provider may not supply is ``... | None`` — it renders ``n/a`` and down-weights
# (never fails) the corresponding scorecard line (PRD §4.1/§4.2).


class Flag(StrEnum):
    """Per-line scorecard verdict (PRD §4.2)."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    NA = "NA"  # metric missing -> down-weighted, not a failure


class ScoreCategory(StrEnum):
    """The six scorecard rows (PRD §4.2)."""

    VALUATION = "VALUATION"
    GROWTH = "GROWTH"
    PROFITABILITY = "PROFITABILITY"
    BALANCE_SHEET = "BALANCE_SHEET"
    ANALYST = "ANALYST"
    EARNINGS_TIMING = "EARNINGS_TIMING"


@dataclass(frozen=True)
class CompanyProfile:
    """Header facts (PRD §4.1 row 1). Everything but ``symbol``/``name`` may be absent."""

    symbol: str
    name: str
    sector: str | None
    industry: str | None
    market_cap: Decimal | None
    currency: str | None
    exchange: str | None


@dataclass(frozen=True)
class FundamentalsSnapshot:
    """The derived numbers we score on — not raw statements (PRD §10, §4.2). Flat and cache-
    friendly: this is what a provider returns and what the repository persists (F4)."""

    symbol: str
    fetched_at: datetime  # UTC, tz-aware
    source: str  # which provider produced it (e.g. "fmp", "yfinance")
    next_earnings_date: date | None

    # Valuation
    pe_ttm: Decimal | None
    pe_fwd: Decimal | None
    price_to_sales: Decimal | None
    peg: Decimal | None
    ev_ebitda: Decimal | None
    price_to_book: Decimal | None

    # Growth
    revenue_yoy: Decimal | None
    eps_yoy: Decimal | None
    revenue_cagr_3y: Decimal | None

    # Profitability
    gross_margin: Decimal | None
    operating_margin: Decimal | None
    net_margin: Decimal | None
    roe: Decimal | None
    fcf_positive: bool | None

    # Balance sheet
    debt_to_equity: Decimal | None
    current_ratio: Decimal | None
    net_debt_to_ebitda: Decimal | None
    interest_coverage: Decimal | None

    # Analyst view
    analyst_rating: str | None
    num_analysts: int | None
    mean_target: Decimal | None

    # Earnings timing
    last_earnings_surprise_pct: Decimal | None


@dataclass(frozen=True)
class NewsItem:
    """One company-specific headline (PRD §4.1 row 9, FR-3)."""

    published_at: datetime  # UTC, tz-aware
    source: str
    headline: str
    url: str
    summary: str | None


@dataclass(frozen=True)
class ScoreLine:
    """One scorecard row: a flag plus the driving value and a one-line note (PRD §4.2)."""

    category: ScoreCategory
    flag: Flag
    value: str | None  # rendered driver, e.g. "P/E 18.2, PEG 1.1" — str: some rows cite two
    note: str


@dataclass(frozen=True)
class Scorecard:
    """The actionable core: the six flag lines plus a headline tally (PRD §4.2). Flags and
    facts only — never a buy/sell/hold recommendation (PRD §2 D5)."""

    lines: tuple[ScoreLine, ...]

    @property
    def tally(self) -> str:
        """Headline count, e.g. ``"4🟢 1🟡 1🔴"`` — derived from ``lines`` so the two can
        never disagree. ``NA`` lines are omitted from the tally."""
        emoji = {Flag.GREEN: "🟢", Flag.YELLOW: "🟡", Flag.RED: "🔴"}
        parts = [
            f"{sum(1 for line in self.lines if line.flag is flag)}{glyph}"
            for flag, glyph in emoji.items()
            if any(line.flag is flag for line in self.lines)
        ]
        return " ".join(parts)


@dataclass(frozen=True)
class CachedFundamentals:
    """What the repository persists for a symbol's fundamentals (F4). Bundles the header
    ``profile`` with the scored ``snapshot`` so a cache hit renders the whole dossier header with
    **zero** external calls (PRD SC-2). Latest-only per symbol (PRD §10)."""

    profile: CompanyProfile
    snapshot: FundamentalsSnapshot


@dataclass(frozen=True)
class NewsCacheEntry:
    """Cached company news for a symbol (F4). ``fetched_at`` drives the short-TTL freshness rule
    (§4.3, ``news_cache_hours``). Latest-only per symbol."""

    symbol: str
    fetched_at: datetime  # UTC, tz-aware
    source: str
    items: tuple[NewsItem, ...]


@dataclass(frozen=True)
class Dossier:
    """The assembled report (PRD §4.1). ``ai_summary`` is filled by the optional stage (F6);
    ``notes`` carries footer DATA_ERROR/STALE annotations (§4.1 row 11)."""

    symbol: str
    profile: CompanyProfile
    snapshot: FundamentalsSnapshot
    scorecard: Scorecard
    news: tuple[NewsItem, ...]
    generated_at: datetime  # UTC, tz-aware
    ai_summary: str | None = None
    notes: tuple[str, ...] = ()
