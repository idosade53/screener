# Architecture — MA150 / ATR Proximity Screener

**Version:** 1.0
**Date:** 2026-08-07
**Status:** Proposed — implements PRD v0.1
**Companion document:** `PRD-stock-screener.md` (requirements, cost model, phase roadmap)

---

## 0. How to read this document

The PRD says *what* the system does and *why* each product decision was made. This document says *how* it is built: the module boundaries, the interfaces between them, the runtime flows, and the rules that keep the Lambda → Raspberry Pi migration a configuration change rather than a rewrite.

Where the PRD is ambiguous or self-contradictory, this document takes a position and marks it **[Reconciliation]**. Those are listed together in §18 so they can be reviewed as a set.

Requirement references (`FR-2`, `§8.3`) point back into the PRD.

---

## 1. Architectural drivers

Five forces shape every decision below. When a trade-off appears, it is resolved in this order.

| # | Driver | Source | Architectural consequence |
|---|---|---|---|
| D1 | **The system must survive a backend swap.** Lambda + DynamoDB today, RPi5 + SQLite later. | PRD §8, §8.5 | Ports-and-adapters. All I/O behind interfaces; two composition roots. |
| D2 | **The market data provider is the most likely component to fail or be replaced.** | PRD FR-2 | `MarketDataProvider` is the narrowest, most rigorously specified port. Degradation is per-symbol, never fatal to the scan. |
| D3 | **Indicator math must be independently verifiable and reusable.** | PRD FR-3, §11 | Indicators are pure functions over a DataFrame. Zero dependencies on storage, transport, or clock. |
| D4 | **Alerts must be low-noise but never silently absent.** | PRD FR-4, Q1, Q3 | Change detection is a first-class pipeline stage with its own persisted state; failure states are surfaced on every message, not on transition. |
| D5 | **Phase 2 back-testing depends on what was *observed*, not what can be re-derived.** | PRD G4, §11 | Every scan persists its inputs (`price`, `sma150`, `atr14`), immutably, forever. Observations are append-only and never corrected. |

---

## 2. Style and top-level structure

**Style:** Hexagonal (ports and adapters), single deployable unit, no internal network hops.

The application is one Python package. It is deployed either as a set of Lambda entry points sharing a container image, or as one long-running process on the Pi. Which of those it is, is decided entirely in the composition root — nothing inside `core/` can observe the difference.

```
                    ┌───────────────────────────────────────┐
                    │            DRIVING SIDE               │
                    │  (things that call into the core)     │
                    ├───────────────────────────────────────┤
   EventBridge ────▶│  scan entrypoint                      │
   / APScheduler    │                                       │
                    │  command entrypoint                   │
   Telegram ───────▶│  (webhook POST / long-poll loop)      │
                    │                                       │
   CLI ────────────▶│  admin entrypoint (backfill, export)  │
                    └──────────────────┬────────────────────┘
                                       │
             ┌─────────────────────────▼─────────────────────────┐
             │                    CORE                           │
             │                                                   │
             │   indicators/     pure math, no I/O                │
             │   screener/       criterion, pipeline, diff        │
             │   domain/         value objects, scan context      │
             │                                                   │
             │   depends on ports only — never on adapters        │
             └─────────────────────────┬─────────────────────────┘
                                       │
                    ┌──────────────────▼────────────────────┐
                    │             DRIVEN SIDE               │
                    │  (things the core calls out to)       │
                    ├───────────────────────────────────────┤
                    │  MarketDataProvider  → yfinance       │
                    │  ScreenerRepository  → DynamoDB       │
                    │                      → SQLite         │
                    │  Notifier            → Telegram HTTP  │
                    │  TradingCalendar     → XNYS           │
                    │  Clock               → system / frozen │
                    └───────────────────────────────────────┘
```

**[Reconciliation R1]** PRD §9's diagram shows APScheduler, long-polling and SQLite; PRD §8 selects EventBridge, webhooks and DynamoDB. The §9 diagram describes the *RPi5 target state*, not the initial deployment. Both are valid instantiations of the structure above — that is the point of the design. Neither is "the" architecture; the ports are.

---

## 3. Package layout and dependency rules

```
src/screener/
  domain/            # value objects, enums, scan context. Pure. No deps.
    models.py            Symbol, Bar, ScanType, ScanContext, SymbolStatus,
                         CriterionResult, ScanResult, ScanSummary, Diff
    errors.py            typed exceptions

  indicators/        # pure math. Depends on: domain, pandas/numpy only.
    sma.py               sma(series: Series, period: int) -> Series
    atr.py               atr(ohlc: DataFrame, period: int) -> Series
    registry.py          name -> callable, for Phase 2 reuse

  screener/          # business logic. Depends on: domain, indicators, ports.
    criterion.py         Criterion protocol + MA150ProximityCriterion
    pipeline.py          orchestration of a single scan
    diff.py              set comparison, entries/exits, baseline rule
    context.py           resolves ScanType -> bar window + price mode

  ports/             # interfaces ONLY. Depends on: domain.
    market_data.py       MarketDataProvider
    repository.py        ScreenerRepository
    notifier.py          Notifier
    calendar.py          TradingCalendar
    clock.py             Clock

  adapters/          # implementations. May depend on anything.
    market_data/yfinance_provider.py
    repository/dynamodb_repository.py
    repository/sqlite_repository.py
    notify/telegram_notifier.py
    notify/formatters.py
    calendar/xnys_calendar.py
    clock/system_clock.py

  bot/               # command parsing. Depends on: ports, screener.
    commands.py          /add /remove /list /status /scan /help
    auth.py              chat-id allowlist, webhook secret check
    dispatch.py          update -> command -> response

  export/
    sqlite_export.py     DynamoDB -> SQLite analytical copy (§9.4)

  config.py          # typed settings, one object, loaded once
  composition/
    lambda_scan.py       AWS entrypoint: scheduled scan
    lambda_webhook.py    AWS entrypoint: Telegram webhook
    lambda_export.py     AWS entrypoint: nightly export
    rpi_main.py          long-running process: APScheduler + long-poll
    cli.py               backfill, one-off scan, export, validate
tests/
  unit/                  indicators, criterion, diff, formatters
  integration/           pipeline against fake adapters + recorded fixtures
  contract/              every adapter against its port's shared test suite
  fixtures/              recorded OHLC JSON, TradingView-verified expectations
```

### The dependency rule

Imports point inward only:

```
composition → adapters → ports → domain
composition → bot      → screener → indicators → domain
```

Three rules, enforced in CI by `import-linter`:

1. `indicators/` may import `domain`, `pandas`, `numpy`. Nothing else. Ever.
2. `screener/` and `domain/` may not import `adapters/` or any third-party SDK (`boto3`, `yfinance`, `requests`, `telegram`).
3. Only `composition/` may instantiate an adapter. Everything else receives its dependencies as constructor arguments.

Rule 3 is what makes PRD §8.5's "path 2" (migrate to SQLite) a contained change. A grep for `boto3` outside `adapters/repository/dynamodb_repository.py` is a build failure.

---

## 4. Domain model

The core types. These are what cross module boundaries; adapters translate to and from their own storage or wire formats at the edge.

```python
class ScanType(StrEnum):
    PRE = "PRE"; OPEN = "OPEN"; CLOSE = "CLOSE"; MANUAL = "MANUAL"

class SymbolStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"   # < 165 completed bars
    DATA_ERROR = "DATA_ERROR"                 # fetch failed after retries
    STALE_PRICE = "STALE_PRICE"               # fell back to previous close

@dataclass(frozen=True)
class ScanContext:
    """Everything the pipeline needs to know about *when* it is running.
    Constructed once, at the top of a scan, from (ScanType, Clock, Calendar).
    No stage below this ever calls the clock again."""
    scan_type: ScanType
    scan_id: str              # "2026-08-07T09:45Z#OPEN" — idempotency key
    ran_at: datetime          # UTC
    trading_day: date         # the ET session this scan belongs to
    indicator_asof: date      # last completed bar feeding SMA/ATR  (§4.3)
    price_mode: PriceMode     # PREMARKET | REGULAR | OFFICIAL_CLOSE
    is_first_of_day: bool     # drives the unconditional baseline (Q1)

@dataclass(frozen=True)
class Indicators:
    sma150: Decimal
    atr14: Decimal
    asof: date                # MUST equal ScanContext.indicator_asof

@dataclass(frozen=True)
class SymbolScanResult:
    symbol: str
    status: SymbolStatus
    price: Decimal | None
    indicators: Indicators | None
    distance_atr: Decimal | None      # (P - SMA150) / ATR14, signed
    in_range: bool                    # False for any non-OK status
```

**Numeric policy.** Prices, MAs and ATRs are `Decimal` at every boundary — persisted as strings in DynamoDB, as `TEXT` in SQLite. `float` exists only inside `indicators/` where pandas requires it, and is converted back on the way out, quantised to 4 decimal places. This matters because SC-3 demands agreement with TradingView "to within rounding", and because Phase 2's back-tester will sum thousands of these.

**Status is exclusive of `in_range`.** A symbol with any status other than `OK` has `in_range = False` and is excluded from the current set. It cannot appear in an entry or exit. This is what stops a data outage from generating a spurious "Exited: everything" message.

---

## 5. Ports

Five interfaces. Each has a shared contract test suite (`tests/contract/`) that every implementation must pass — including the fakes used in integration tests. A fake that passes the same suite as the real adapter is a fake you can trust.

### 5.1 `MarketDataProvider`

The riskiest port (D2), so the most tightly specified.

```python
class MarketDataProvider(Protocol):
    def fetch_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> BarFetchResult: ...
    """Batched (FR-2). MUST NOT loop per symbol.
       Partial success is normal: returns bars-by-symbol AND failures-by-symbol."""

    def fetch_quotes(
        self, symbols: Sequence[str], mode: PriceMode
    ) -> QuoteFetchResult: ...
    """PREMARKET may return no trade for a symbol -> caller falls back (Q2).
       The provider does NOT do the fallback; it reports absence honestly."""

    def validate_symbol(self, symbol: str) -> bool: ...
    """Used by /add before a symbol enters the universe (FR-1)."""
```

Contract obligations every implementation must satisfy:

- Never raises for a single-symbol failure. Individual failures are data in the result, not control flow.
- Never returns a partial or provisional bar for a session that has not closed. If the provider offers today's incomplete bar, the adapter drops it. This is the mechanism that enforces PRD §4.3.
- Returns unadjusted OHLC. Adjustment is the caller's concern (§7.3).
- Retries (3×, exponential backoff, jitter) live *inside* the adapter. The core never retries.
- Timeouts are bounded such that a 200-symbol fetch cannot exceed the NFR budget of 60 s.

**Anti-corruption note.** `yfinance` returns a MultiIndex DataFrame whose shape changes with the number of symbols requested, silently returns empty frames for delisted tickers, and occasionally returns a `NaN` close. All of that is normalised inside the adapter. Nothing shaped like a yfinance object escapes into the core — otherwise a provider swap becomes a rewrite, which is exactly what D2 forbids.

### 5.2 `ScreenerRepository`

Deliberately coarse-grained. Methods correspond to the access patterns in PRD §8.3 — not to tables, and not to CRUD. A fine-grained repository would leak the relational model into the core and make the DynamoDB implementation a hand-written ORM.

```python
class ScreenerRepository(Protocol):
    # universe
    def get_universe(self) -> list[UniverseMember]: ...
    def add_symbols(self, symbols: Sequence[str]) -> None: ...
    def remove_symbol(self, symbol: str) -> None: ...

    # bars
    def get_bars(self, symbol: str, since: date) -> list[Bar]: ...
    def upsert_bars(self, symbol: str, bars: Sequence[Bar]) -> None: ...
    def latest_bar_date(self, symbol: str) -> date | None: ...
    def delete_bars(self, symbol: str) -> None: ...          # split re-fetch

    # indicator cache
    def get_indicators(self, symbols: Sequence[str], asof: date
                       ) -> dict[str, Indicators]: ...
    def put_indicators(self, values: Mapping[str, Indicators]) -> None: ...

    # scans
    def latest_scan(self) -> ScanSummary | None: ...
    def scans_on(self, day: date) -> list[ScanSummary]: ...
    def save_scan(self, summary: ScanSummary,
                  results: Sequence[SymbolScanResult]) -> None: ...
    def try_claim_scan(self, scan_id: str) -> bool: ...       # idempotency

    # alerts
    def record_alert(self, scan_id: str, message: str,
                     status: DeliveryStatus) -> None: ...
```

`save_scan` writes the summary and all per-symbol observations as one logical operation. In DynamoDB this is batched writes plus a final summary put; in SQLite it is one transaction. The core does not know or care which.

### 5.3 `Notifier`, `TradingCalendar`, `Clock`

```python
class Notifier(Protocol):
    def send(self, message: str) -> DeliveryStatus: ...
    """Chunking at 4096 chars is the adapter's job. Retries 3x internally.
       MUST NOT raise — a failed alert never crashes the scheduler (FR-5)."""

class TradingCalendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def previous_trading_day(self, d: date) -> date: ...
    def session_close(self, d: date) -> time: ...   # 13:00 ET on half-days

class Clock(Protocol):
    def now(self) -> datetime: ...                  # always tz-aware UTC
```

`Clock` exists for one reason: M6 requires correct behaviour across a weekend, a holiday and a half-day, tested with a frozen clock. A single `datetime.now()` call anywhere outside a `Clock` implementation makes that test unwritable.

---

## 6. Runtime flow — the scan pipeline

The heart of the system. Ten stages, each a pure-ish function taking the accumulated state and returning the next state. Written this way so a stage can be tested, replayed, or (in Phase 2) skipped.

```
  ┌─ 1. RESOLVE CONTEXT ────────────────────────────────────────────┐
  │  (ScanType, Clock, Calendar) -> ScanContext                     │
  │  • not a trading day  -> exit 0, log, no alert                  │
  │  • half-day + CLOSE   -> shift, or run early per calendar       │
  │  • compute scan_id, indicator_asof, price_mode, is_first_of_day │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 2. CLAIM ──────────────────────────────────────────────────────┐
  │  repo.try_claim_scan(scan_id)  — conditional write              │
  │  • already claimed -> exit 0 silently  (duplicate invocation)   │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 3. LOAD UNIVERSE ──────────────────────────────────────────────┐
  │  repo.get_universe() -> active symbols                          │
  │  • empty universe -> send "universe is empty", exit 0           │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 4. REFRESH BARS  (CLOSE only) ─────────────────────────────────┐
  │  delta fetch since latest_bar_date per symbol, batched          │
  │  • split detection (§7.3) -> full refetch for affected symbols  │
  │  • staleness guard (§7.2) -> ABORT scan, ⚠️ SYSTEM alert        │
  │  • per-symbol failure -> status DATA_ERROR, continue            │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 5. INDICATORS ─────────────────────────────────────────────────┐
  │  CLOSE : compute from bars (pure), then repo.put_indicators()   │
  │  PRE   : repo.get_indicators(asof=indicator_asof)               │
  │  OPEN  : ditto                                                  │
  │  • cache miss or asof mismatch -> recompute from bars (§7.4)    │
  │  • < 165 bars -> INSUFFICIENT_DATA                              │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 6. PRICES ─────────────────────────────────────────────────────┐
  │  provider.fetch_quotes(symbols, context.price_mode)             │
  │  • no quote in PREMARKET -> previous close, status STALE_PRICE  │
  │  • no quote at all       -> DATA_ERROR                          │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 7. EVALUATE ───────────────────────────────────────────────────┐
  │  for each registered Criterion: evaluate(bars|indicators, price)│
  │  combine per ALERT_COMBINATOR (ALL in Phase 1)                  │
  │  -> SymbolScanResult per symbol                                 │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 8. PERSIST ────────────────────────────────────────────────────┐
  │  repo.save_scan(summary, results)   ← happens BEFORE notify     │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 9. DIFF ───────────────────────────────────────────────────────┐
  │  previous = repo.latest_scan().in_range  (one item read)        │
  │  send if: sets differ  OR  is_first_of_day  OR  MANUAL          │
  └────────────────────────────┬────────────────────────────────────┘
                               ▼
  ┌─ 10. NOTIFY ────────────────────────────────────────────────────┐
  │  format -> chunk -> notifier.send() -> repo.record_alert()      │
  │  failure here is logged, never raised                           │
  └─────────────────────────────────────────────────────────────────┘
```

### Ordering invariants

Three orderings are load-bearing and must not be rearranged.

1. **Persist before notify (8 before 10).** A crash between them costs one missed alert. The reverse ordering costs a permanent hole in the observation record that Phase 2 depends on (D5). Missed alerts are recoverable; missing history is not.
2. **Claim before work (2 before 4).** Under reserved concurrency 1, a throttled EventBridge invocation is retried. Without the claim, a retry re-runs a completed scan and can re-alert.
3. **Diff after persist (9 after 8).** The diff reads `latest_scan()`, which must not yet be the scan currently running. In DynamoDB, `save_scan` writes the summary item last and `latest_scan` is a `Limit 1, ScanIndexForward=false` query — so the diff stage is given the previous summary explicitly, captured in stage 9's input rather than re-queried. **This is the single subtlest correctness point in the pipeline: capture `previous` before `save_scan` commits the summary.**

### Change detection rules (FR-4, Q1)

```
send  ⟸  scan_type == MANUAL                       # /scan always reports
     ∨  is_first_of_day                            # daily baseline (Q1)
     ∨  current_in_range_set ≠ previous_in_range_set
```

- `previous` is the most recent scan of *any* type, including `MANUAL`. A manual scan therefore updates the baseline; this is intended, and is why `/scan` output labels itself as manual.
- `is_first_of_day` is computed by `repo.scans_on(trading_day)` returning empty — a `begins_with` query on the date prefix, one item read.
- Non-`OK` symbols never enter either set, so a data outage produces no entries or exits — only a footer.

---

## 7. Data architecture

### 7.1 Logical vs physical

The logical model in PRD FR-6 is the contract. Two physical realisations implement it:

| | DynamoDB (operational, Lambda) | SQLite (analytical + RPi5) |
|---|---|---|
| Role | Serves live scans and commands | Phase 2 back-test, Phase 3 dashboard, RPi5 operational store |
| Written by | The scan pipeline | Nightly export (§9.4); later, the pipeline itself |
| Access | `ScreenerRepository` (Dynamo impl) | `ScreenerRepository` (SQLite impl) + raw SQL for analysis |
| Schema | Single table, §8.3 of the PRD | Exactly the DDL in PRD FR-6 |

The SQLite file is not a convenience — it is the migration artefact. On the day the Pi takes over, the file already exists and is already current (PRD §8.5, path 2). The only work is pointing the composition root at `SqliteScreenerRepository`.

**[Reconciliation R2]** PRD FR-6 quotes ~20 MB/year; §8.3 quotes ~15 MB/year. The difference is the `daily_bars` table (~5 MB/year), which DynamoDB's §8.3 estimate appears to exclude. Assume **~20 MB/year including bars**. Neither figure is close to any limit, so nothing depends on resolving it; recorded so the numbers stop disagreeing.

### 7.2 Freshness and the staleness guard

Before any evaluation, the pipeline asserts:

```
max(bar_date across universe) == calendar.previous_trading_day(trading_day)   # PRE, OPEN
max(bar_date across universe) == trading_day                                  # CLOSE
```

Violation aborts the scan and emits `⚠️ SYSTEM` (FR-2). It does **not** fall back to older bars. Alerting on a stale band is worse than not alerting: the user cannot distinguish it from a live signal, which is the same reasoning behind Q2's `~` marker.

Two symbols failing is a per-symbol `DATA_ERROR`. The *universe-wide* newest bar being stale is a provider outage and is fatal to the scan. The threshold between them: if more than 50% of the universe returns no fresh bar, treat it as an outage, not as 75 individual failures.

### 7.3 Corporate actions

Bars are stored unadjusted (FR-2), because ATR on adjusted prices is wrong for recent history. That makes splits a correctness hazard for SMA150.

Detection, run at every `CLOSE`:

```
for each symbol:
    stored   = repo.get_bars(symbol, since=trading_day - 5d)
    fetched  = provider bars for the same window
    ratio    = fetched.close[d] / stored.close[d]   for the overlapping d
    if abs(ratio - 1) > 0.02 and ratio ≈ a simple fraction (1/2, 1/3, 2/3, 1/4, 1/10, ...):
        -> split detected
```

On detection: `delete_bars(symbol)`, refetch the full 2-year window, invalidate `IND#<symbol>`, recompute. The symbol is marked `DATA_ERROR` for that one scan rather than evaluated mid-rewrite — one missed observation is cheaper than one wrong one.

The 2% threshold with a fraction check avoids treating a large gap-down as a split. False negatives (an unrecognised ratio) are caught by the same check on the following day, since the discontinuity persists in stored data.

### 7.4 The indicator cache

PRD §8.3 establishes this as the main runtime optimisation: SMA150 and ATR14 only change when a daily bar completes, so they are computed once at `CLOSE` and read by `PRE` and `OPEN`.

The architectural requirement is that **the cache is never trusted blindly**. Every read validates `Indicators.asof == ScanContext.indicator_asof`. On mismatch — which happens whenever a `CLOSE` scan failed — the pipeline recomputes from bars and logs a `cache_miss` metric. `PRE` and `OPEN` are therefore slower but still correct after a failed `CLOSE`, rather than silently correct-looking and wrong.

This is why `indicators/` must stay pure: the same function serves the cache-fill path, the cache-miss path, and Phase 2's back-tester over historical bars.

### 7.5 Immutability

| Entity | Mutability |
|---|---|
| `UNIVERSE` members | Mutable — `active` flag flips, never deleted (preserves history of what was watched when) |
| `BAR#<sym>` | Upsert on `(symbol, date)` — corrections and split refetches overwrite |
| `IND#<sym>` | Overwrite per day; a derived cache, reconstructible from bars |
| `SCAN`, `HIST#<sym>`, alerts | **Append-only, never updated, never deleted** |

`/remove` sets `active = false`; it does not delete. Otherwise removing a symbol would orphan its observations and break Phase 2's per-symbol history query.

---

## 8. Cross-cutting concerns

### 8.1 Time

One rule: **UTC internally, ET for scheduling and display, IDT never.**

- Every stored timestamp is UTC ISO-8601 with a `Z` suffix. Lexicographic sort equals chronological sort, which is what makes the `SCAN` sort key work.
- Trading-day arithmetic goes through `TradingCalendar`, never through date subtraction. A weekend, a Thanksgiving, and a July 3rd half-day are all "the previous trading day" in different ways.
- Message rendering converts to ET at the formatter, the only place a timezone appears in output. The user reads ET (PRD §3) even though they are in IDT.
- DST is handled by EventBridge Scheduler's named-timezone cron (`America/New_York`) on Lambda and by APScheduler's timezone support on the Pi. Neither is hand-rolled.

### 8.2 Configuration

One frozen `Settings` object, constructed in the composition root, injected downward. No module reads the environment directly.

| Setting | Default | Notes |
|---|---|---|
| `BAND_ATR_MULT` | `1.5` | Global, per Q4 |
| `SMA_PERIOD` / `ATR_PERIOD` | `150` / `14` | |
| `MIN_BARS_REQUIRED` | `165` | Derived, but explicit so tests can lower it |
| `UNIVERSE_CAP` | `300` | |
| `SCAN_TIMES_ET` | `09:00, 09:45, 20:15` | Config, not code (PRD §5) |
| `ALERT_COMBINATOR` | `ALL` | Phase 1 has one criterion |
| `TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN` | — | Secret |
| `TELEGRAM_WEBHOOK_SECRET` | — | Secret |
| `REPOSITORY_BACKEND` | `dynamodb` | `sqlite` on the Pi — **the migration switch** |
| `PROVIDER` | `yfinance` | |

Secrets come from SSM Parameter Store (SecureString) on Lambda, `.env` on the Pi. The `Settings` loader has two implementations behind one signature; nothing downstream knows which ran.

`REPOSITORY_BACKEND` deserves emphasis. If the design is correct, flipping that one string plus supplying a file path is the entire data-layer half of the RPi migration.

### 8.3 Failure taxonomy

The system's central behavioural question is: *what does each failure degrade to?* Answering it once, here, prevents ad-hoc `try/except` from accumulating.

| Failure | Scope | Behaviour | User sees |
|---|---|---|---|
| One symbol's bars unavailable | Symbol | `DATA_ERROR`, excluded from sets | Footer, every message (Q3) |
| One symbol < 165 bars | Symbol | `INSUFFICIENT_DATA` | Footer, every message |
| No pre-market quote | Symbol | Previous close, `STALE_PRICE` | `~` price prefix (Q2) |
| >50% of universe fails | Scan | Abort before evaluation | `⚠️ SYSTEM` |
| Newest bar older than expected | Scan | Abort before evaluation | `⚠️ SYSTEM` |
| Indicator cache miss / stale | Scan | Recompute from bars, continue | Nothing (metric only) |
| Split detected | Symbol | Refetch, skip this scan for that symbol | Footer |
| Repository write fails | Scan | Retry, then fail loudly | `⚠️ SYSTEM` |
| Telegram send fails | Alert | Retry 3×, log, record `FAILED` | Nothing (by definition) |
| Scan didn't run at all | Scan | Detected by the next scan's gap check | `⚠️ SYSTEM` |
| Unauthorised chat | Command | Ignored silently | Nothing |

Two principles hold this together:

- **Symbol-scoped failures never escalate to scan-scoped.** One dead ticker must not cost 149 good observations.
- **Scan-scoped failures never degrade into a plausible-looking alert.** If the system cannot be sure the band is current, it says so instead of guessing.

The missed-scan check (row 10) runs at the start of every scan: if `scans_on(trading_day)` is missing a scan type that should already have run, prepend a `⚠️ SYSTEM missed <TYPE>` line. This is the mechanism behind PRD's reliability NFR — the system reports its own gaps rather than relying on external monitoring.

### 8.4 Idempotency and concurrency

- `scan_id` is deterministic: `{trading_day}T{scheduled_time}Z#{TYPE}`. `try_claim_scan` is a conditional put with `attribute_not_exists(PK)`. Retried invocations exit silently at stage 2.
- Reserved concurrency 1 on the scan function; the claim is belt-and-braces for the case where a manual `/scan` and a scheduled scan coincide.
- `MANUAL` scans use `{timestamp}#MANUAL` and so never collide.
- The webhook function has no such constraint — Telegram may redeliver an update, so command handling is idempotent by construction: `/add AAPL` twice is one symbol, `/remove` of an absent symbol is a no-op with a friendly reply.
- On the Pi, APScheduler runs with `max_instances=1` and `coalesce=True`, which reproduces reserved-concurrency semantics in-process.

### 8.5 Observability

- **Structured JSON logs**, one event per pipeline stage, always carrying `scan_id`, `scan_type`, `trading_day`. CloudWatch on Lambda; rotating file on the Pi (`RotatingFileHandler`, 10 MB × 5).
- **Metrics** emitted as log lines with a fixed prefix, so they work identically on both targets without a metrics backend: `scan_duration_ms`, `symbols_scanned`, `in_range_count`, `error_count`, `cache_miss_count`, `provider_retry_count`, `alert_send_ms`.
- **Heartbeat**: appended to the `CLOSE` message; if three consecutive trading days pass with no message at all, an "all quiet" ping is sent (FR-7). Implemented in the diff stage, since it is the only stage that knows a message was suppressed.
- **External liveness**: on the Pi, a healthcheck ping to an external uptime monitor after each successful scan. The system cannot report its own death; only an outside observer can.

### 8.6 Security

Small surface, but it is on the public internet during the Lambda phase.

- Lambda Function URL with `AuthType: NONE` — unavoidable, since Telegram cannot sign AWS requests. Compensating controls:
  1. `setWebhook` is registered with a `secret_token`; every request must present a matching `X-Telegram-Bot-Api-Secret-Token` header. Non-matching → 401 before any parsing.
  2. `update.message.chat.id` must equal `TELEGRAM_CHAT_ID`. Non-matching → 200 with empty body, silently (FR-1). Returning 200 avoids Telegram retry storms.
  3. The webhook function's IAM role has write access to the universe items only — it cannot write scan history or read secrets beyond the bot token.
- The bot token is read at cold start and never logged. Log formatters redact any string matching the token.
- Symbol input from `/add` is validated against `^[A-Z][A-Z0-9.\-]{0,9}$` before it reaches the provider or the repository. There is no SQL on the Lambda path, but the SQLite path is parameterised throughout regardless.
- No inbound port on the Pi at all — long-polling removes the public endpoint, which is a genuine security improvement from the migration, not just an operational one.

---

## 9. Deployment architecture

### 9.1 Lambda topology

Three functions, one container image, distinct handlers:

| Function | Trigger | Concurrency | IAM |
|---|---|---|---|
| `screener-scan` | EventBridge Scheduler × 3 rules (`America/New_York` cron) | Reserved 1 | RW on table, read SSM, no internet ingress |
| `screener-webhook` | Function URL (POST) | Default | RW on universe items only, read SSM |
| `screener-export` | EventBridge, daily post-`CLOSE` | Reserved 1 | Read table, write S3 |

Sharing one image means one build, one ECR repository, one dependency set — and keeps the ECR line item (PRD §13.1, the only non-zero cost) at a single image rather than three.

### 9.2 Image strategy

The image is the largest cost in the system, so it is treated as an architectural artefact, not a build detail:

- Multi-stage build; only the virtualenv and `src/` reach the final layer.
- `--no-compile`, `__pycache__` and tests stripped, no `matplotlib`/`scipy` (pull them in transitively and the image roughly doubles).
- Target < 300 MB.
- ECR lifecycle policy retaining 2 images — **required**, not optional. Without it this line item grows without bound (PRD §13.1).
- Built for `linux/amd64` now and `linux/arm64` later from the same Dockerfile via `buildx`.

### 9.3 Single-table design

Per PRD §8.3, unchanged. The architectural point worth restating: **all five Phase 1 access patterns are satisfied without a GSI**, and change detection reads exactly one item because the previous scan's `inrange` list is stored inline on the summary. No diffing query, no scan, no consistency question.

Watch item: DynamoDB's 400 KB item limit. A `SCAN` summary carrying 300 symbols inline is roughly 3 KB — two orders of magnitude of headroom, but this is the item that would break first if the universe cap were ever raised dramatically. If `inrange` ever approaches 100 KB, split it into a separate `SCANSET#` item.

### 9.4 The analytical export

After each successful `CLOSE`, `screener-export` materialises the PRD FR-6 DDL into `screener.db` and uploads it to S3.

- Full rebuild, not incremental. At ~20 MB/year, a full rebuild is seconds of work and eliminates an entire class of incremental-sync bug.
- Written to a temp key, then copied to `screener-latest.db` — readers never see a half-written file.
- Retention: 7 daily + 12 monthly snapshots.
- The export is a *derived artefact*. It is never written back to; DynamoDB is authoritative until the day the Pi takes over, at which point the roles invert and the last export becomes the seed.

### 9.5 Migration to RPi5

The whole design exists to make this table short.

| Concern | Lambda | RPi5 | Code change |
|---|---|---|---|
| Scheduling | EventBridge Scheduler | APScheduler in-process | Composition root only |
| Command transport | Webhook (Function URL) | Long-polling | New `Notifier`/transport wiring, no core change |
| Repository | `DynamoDbScreenerRepository` | `SqliteScreenerRepository` | **One new adapter** (the acknowledged cost, PRD §8.5) |
| Secrets | SSM | `.env` | Settings loader only |
| Image | `linux/amd64` on ECR | `linux/arm64` under Compose | Build target only |
| Indicators, criterion, pipeline, diff, formatters, bot commands | | | **None** |

Two operational conditions on the Pi: database on SSD/NVMe rather than SD card (SD wear-out under daily writes is a when, not an if), and an external uptime monitor, since home power and internet are now in the dependency chain.

**[Reconciliation R3]** PRD M7 defines "done" as *Docker Compose on a VPS*, which is a third target not discussed in §8. Read M7 as "the deployment milestone", target-agnostic: secrets externalised, nightly backup, 5 consecutive clean trading days. The target for Phase 1 is Lambda per §8.1; the same criteria apply unchanged to a VPS or the Pi.

---

## 10. Testing architecture

Testability is an NFR, and the layering above is what buys it. Four tiers:

| Tier | Scope | Dependencies | Runs |
|---|---|---|---|
| **Unit** | `indicators/`, `criterion`, `diff`, `formatters`, `context` | None. Pure functions. | Every commit, < 5 s |
| **Contract** | Every adapter vs its port's shared suite | DynamoDB Local, temp SQLite, recorded HTTP | Every commit |
| **Integration** | Full pipeline, fake adapters, recorded fixtures, frozen clock | No network | Every commit |
| **Smoke** | Real provider, real Telegram, throwaway chat | Network | Manual, pre-deploy |

Specific obligations:

- **SC-3 (TradingView agreement)** is a *test*, not a review step: 10 symbols × hand-checked SMA150/ATR14 values committed as fixtures. The ATR fixture must include a seeded RMA sequence long enough that Wilder smoothing has converged, since a wrong seed produces values that are close for weeks and diverge slowly.
- **M6 (calendar correctness)** is exercised with a frozen clock across: a Friday→Monday weekend, Thanksgiving, the July 3rd half-day, and a Monday holiday. `TradingCalendar` is the only thing under test; the pipeline is faked.
- **Change detection** is tested as a scripted sequence of ≥ 3 scans asserting exactly which produce messages — including the first-of-day baseline and a manual scan mid-sequence.
- **Failure injection**: every row of the §8.3 table has a test that induces it and asserts the degradation, particularly that a per-symbol failure never empties the in-range set.
- **No network in CI**, per the PRD. Provider responses are recorded fixtures; the yfinance adapter's contract test replays them.

---

## 11. Extension points

Three future changes are anticipated. Each has a named seam so it does not become a refactor.

### 11.1 Second criterion (PRD §10)

`Criterion` is a protocol; the pipeline runs a registry. Adding RSI means: a new pure function in `indicators/`, a new `Criterion` implementation, one registry entry, and a formatter update. The pipeline, repository and diff are untouched.

Storage impact: observations gain a `criterion` discriminator (`HIST#<sym>` items gain a `crit` attribute; the SQLite `scan_results` gains a column). The set used for change detection becomes "symbols passing the configured combination", so `diff.py` is unchanged — it already operates on an opaque set.

### 11.2 Provider replacement (PRD §13.4 — the real risk)

The most likely change, and the one D2 is built for. A paid provider means one new `MarketDataProvider` implementation passing the existing contract suite, plus a config flip. The contract suite is the specification; if a new provider passes it, the core cannot tell the difference.

The one thing that would break this seam: letting provider-shaped objects (yfinance DataFrames, provider-specific symbol formats) leak past the adapter. That is why §3's rule 2 is CI-enforced.

### 11.3 Phase 2 back-tester (PRD §11)

Consumes the SQLite export, imports `indicators/` and `screener/criterion.py` directly, and never touches DynamoDB, Telegram or the provider. This is only possible because those modules have no I/O — which is D3's entire justification.

The load-bearing decision already made in Phase 1: observations store `price`, `sma150` and `atr14`, not just the boolean. Any rule variant (band width, entry side, ATR multiples) is reconstructible from recorded history without refetching. If only `in_range` were stored, Phase 2 would be limited to re-deriving signals from adjusted prices — a different and worse experiment.

---

## 12. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | yfinance degrades or breaks | System stops working | `MarketDataProvider` contract suite makes swapping a day's work; staleness guard fails loudly rather than quietly |
| R2 | Pre/post-market coverage is thin on a free feed | `PRE` scan is mostly `~` fallbacks | Q2's marker makes it visible; if the majority of a `PRE` scan is stale, the scan is low-value and worth reconsidering |
| R3 | Split detection misses an unusual ratio | Corrupted SMA150 for one symbol | Discontinuity persists in stored data, so the next day's check catches it; distance values are stored, making a corrupted symbol visually obvious in the alert |
| R4 | Accidental on-demand DynamoDB table | Billed from the first write | Provisioned 5/5 explicitly in IaC, billing alarm at $1 |
| R5 | ECR images accumulate | Unbounded cost growth | Lifecycle policy is part of the deploy, not a manual step |
| R6 | Wilder seed implemented wrong | Values plausible but subtly off, forever | Fixture test with a converged sequence; SC-3 spot audit |
| R7 | RPi5 SD card wear | Silent data loss | SSD/NVMe requirement; nightly file-copy backup |

---

## 13. Decisions recorded

Compressed ADR log — the *architectural* decisions, distinct from the product decisions in PRD §14.

| # | Decision | Alternative rejected | Why |
|---|---|---|---|
| A1 | Ports and adapters | Direct calls, refactor later | The RPi migration is planned, not hypothetical. Retrofitting boundaries after the fact is the expensive version. |
| A2 | Indicators are pure functions | Methods on a data-access class | Reused unchanged by Phase 2; unit-testable without fixtures or fakes |
| A3 | Coarse-grained repository (access patterns, not CRUD) | Table-per-method | Prevents the relational model leaking into core and turning the Dynamo adapter into a hand-written ORM |
| A4 | `ScanContext` resolved once, injected | Ad-hoc `now()` calls | Makes M6's frozen-clock tests possible at all |
| A5 | Persist before notify | Notify first for lower latency | Missing history is unrecoverable and Phase 2 depends on it; a missed alert is not |
| A6 | Deterministic `scan_id` + conditional claim | Trust reserved concurrency | Retried invocations are normal on Lambda; duplicate alerts violate SC-2 |
| A7 | Symbol failures never escalate to scan failures | Fail fast | One dead ticker must not cost 149 good observations |
| A8 | Indicator cache validated on every read | Trust the cached value | A failed `CLOSE` would otherwise make `PRE`/`OPEN` silently wrong the next morning |
| A9 | Full-rebuild nightly export | Incremental sync | 20 MB/year makes rebuild trivial and removes a class of sync bugs |
| A10 | One image, three functions | One image per function | ECR storage is the only non-zero cost in the system |
| A11 | `Decimal` at all boundaries | `float` throughout | SC-3 demands rounding-level agreement; Phase 2 aggregates thousands of values |
| A12 | Soft-delete symbols (`active=false`) | Hard delete | Hard delete orphans observations and breaks Phase 2's per-symbol history |

---

## 14. Reconciliations against the PRD

Flagged inline above; collected here for review.

| # | Location | Issue | Position taken |
|---|---|---|---|
| R1 | PRD §9 vs §8 | §9's diagram shows SQLite/APScheduler/polling; §8 selects DynamoDB/EventBridge/webhook | §9 describes the RPi5 target state. Both are valid instantiations; the ports are the architecture. |
| R2 | PRD FR-6 vs §8.3 | ~20 MB/year vs ~15 MB/year | Use ~20 MB/year (§8.3 appears to omit `daily_bars`). Nothing depends on it. |
| R3 | PRD M7 vs §8 | M7 says "Docker Compose on VPS"; §8 targets Lambda | Read M7 as target-agnostic deployment criteria. Phase 1 target is Lambda. |
| R4 | PRD §13 heading | "§13. Cost Model" is referenced elsewhere as "open questions (§13)" | §13 is the cost model; resolved decisions are §14. Front-matter reference is stale. |

---

## 15. Build order

Milestone sequence matching PRD §15, annotated with the architectural dependency that makes each one possible.

| # | Deliverable | Unblocked by | Done when |
|---|---|---|---|
| M0 | Domain types + port definitions + contract test skeletons | — | `import-linter` rules pass on an empty implementation |
| M1 | Indicator core | M0 | SMA/ATR match TradingView fixtures to 4 dp |
| M2 | Data layer: yfinance adapter + repository adapter | M0, M1 | 150 symbols cached; delta refresh and split detection tested |
| M3 | Pipeline end-to-end via CLI | M1, M2 | Persisted observations from a local invocation |
| M4 | Formatters + diff + Telegram out | M3 | Scripted 3-scan sequence produces exactly the expected messages |
| M5 | Bot commands in | M2, M4 | `/add`,`/remove`,`/list`,`/status`,`/scan`; unauthorised chats ignored |
| M6 | Calendar + scheduling | M0 (Clock), M3 | Frozen-clock tests pass a weekend, holiday and half-day |
| M7 | Deploy | all | Secrets externalised, nightly backup, 5 consecutive clean trading days |

M0 is not in the PRD and is the one addition: defining the ports before the adapters is what makes M1–M6 independently testable and parallelisable.
