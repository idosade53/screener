# PRD — MA150 / ATR Proximity Screener with Telegram Alerts

**Version:** 0.1 (draft)
**Date:** 2026-08-07
**Owner:** [you]
**Status:** Awaiting sign-off on open questions (§13)

---

## 1. Summary

A scheduled service that watches a user-managed list of US-listed stocks, computes two daily-bar indicators (SMA150, ATR14), and reports which symbols are currently trading *within 1.5 × ATR of their 150-day moving average*. Results are pushed to a private Telegram chat three times per trading day, but only when the set of qualifying symbols has changed since the previous scan.

Phase 1 ships the screener + alerting. Paper trading and the statistics dashboard are designed here but not built.

---

## 2. Goals & Non-Goals

### Goals (Phase 1)
- G1. Evaluate a 50–200 symbol universe against a defined technical criterion, three times per trading day.
- G2. Deliver actionable, low-noise alerts to Telegram.
- G3. Allow the universe to be managed entirely from Telegram — no redeploy to add a ticker.
- G4. Persist every scan result, so Phase 2 can back-test the method against real recorded history rather than re-derived data.

### Non-Goals (Phase 1)
- Order execution, broker integration, or real money.
- Paper trading engine (Phase 2 — §11).
- Web dashboard (Phase 3 — §12).
- Multi-user support, authentication, or any public-facing surface.
- Non-US exchanges.
- Intraday indicators (all indicator math is on daily bars).

### Success criteria
- SC-1. Three scans per trading day complete with ≥99% success over a rolling 30 days.
- SC-2. Zero duplicate alerts for an unchanged in-range set.
- SC-3. Indicator values match a manual TradingView/Excel check to within rounding on a 10-symbol spot audit.
- SC-4. Adding a symbol via Telegram takes effect on the very next scan.

---

## 3. Users & Context

Single user, technically capable, based in Israel (UTC+3), trading US equities. All schedules are authored in US market time and converted; the user never reasons about ET vs IDT. Alerts arriving at ~03:15 local (the post-close scan) are expected and acceptable — they are read in the morning.

---

## 4. The Criterion (Phase 1, indicator #1)

### 4.1 Definitions

| Term | Definition |
|---|---|
| `SMA150` | Simple moving average of the **closing price** over the last 150 completed daily bars. |
| `ATR14` | Average True Range, 14 periods, **Wilder smoothing** (RMA), on daily bars. |
| `P` | Reference price for the scan (see §4.3). |
| `distance` | `(P − SMA150) / ATR14` — signed, expressed in ATR units. |
| **In range** | `abs(P − SMA150) ≤ 1.5 × ATR14`, i.e. `abs(distance) ≤ 1.5`. |

The band is **symmetric**: a stock qualifies whether it sits above or below the MA. Sign is preserved in the alert so the user can tell which side.

### 4.2 True Range recap (for implementation clarity)

```
TR   = max( high − low,
            abs(high − prev_close),
            abs(low  − prev_close) )
ATR14 = Wilder RMA of TR over 14 periods
```

Seed the RMA with a simple mean of the first 14 TR values, then `ATR_t = (ATR_{t-1} × 13 + TR_t) / 14`. This must match the standard TradingView `atr(14)` output.

### 4.3 Which bars, which price — critical rule

Indicators are **always computed from completed daily bars**. The live price is tested against a band that does not move during the session. Without this rule the band jitters intraday and the "changed since last scan" logic becomes noise.

| Scan | Indicator input | Reference price `P` |
|---|---|---|
| Pre-market | bars through **previous** close | last pre-market trade (extended hours) |
| Post-open | bars through **previous** close | last regular-session trade |
| Post-close | bars through **today's** close (today's bar now complete) | today's official close |

### 4.4 Data sufficiency
A symbol requires ≥165 completed daily bars (150 for the SMA + 14 for the ATR seed + 1). Symbols with insufficient history are marked `INSUFFICIENT_DATA`, excluded from the in-range set, and reported once in a separate "excluded" line — not repeated every scan.

---

## 5. Schedule

Three scans per **US trading day**. Non-trading days (weekends + NYSE holidays + early closes) are skipped; early-close days shift the post-close scan.

| ID | Trigger | Time (ET) | Approx. local (IDT) |
|---|---|---|---|
| `PRE` | Pre-market, before the open | 09:00 | 16:00 |
| `OPEN` | Shortly after the open | 09:45 | 16:45 |
| `CLOSE` | After the extended session closes | 20:15 | 03:15 |

- Holiday/half-day calendar via `exchange_calendars` or `pandas_market_calendars` (XNYS).
- The 15-minute delay after 20:00 ET gives the data provider time to settle the official close.
- Schedule times live in config, not code.

---

## 6. Functional Requirements

### FR-1 — Universe management (Telegram-driven)
- `/add TSLA MSFT` — add one or more symbols; validate each against the data provider before accepting; reply with accepted/rejected.
- `/remove TSLA` — remove.
- `/list` — current universe, count.
- `/status` — last scan time, last result count, next scheduled scan, data freshness.
- `/scan` — force an out-of-band scan (result sent regardless of change-detection).
- `/help`.
- Symbols normalised to uppercase, deduplicated. Universe cap: 300 (soft guard against accidental bulk paste).
- Only the configured `TELEGRAM_CHAT_ID` may issue commands; all other chats are ignored silently.

### FR-2 — Data ingestion
- Provider: `yfinance` (free). Provider access sits behind a `MarketDataProvider` interface so it can be swapped for a paid feed without touching the indicator or alerting layers. **This is the single most likely component to need replacing** — free feeds are unreliable and pre/post-market coverage is thin.
- Daily history: batched download (one call for all symbols, `period="2y"`, `auto_adjust=False`), not per-symbol loops.
- Split/dividend handling: use **unadjusted** OHLC for ATR, but detect splits and refetch full history when a split is detected, so the SMA isn't corrupted by a price discontinuity.
- Retry with exponential backoff (3 attempts). Partial failure is tolerated: symbols that fail are reported as `DATA_ERROR`, the rest of the scan proceeds.
- Staleness guard: if the newest daily bar is older than the last expected trading day, abort the scan and send a system alert rather than alerting on stale data.
- Cache daily bars in SQLite; only fetch the delta since the last stored bar.

### FR-3 — Indicator engine
- Pure functions over a price series: `sma(series, 150)`, `atr(ohlc, 14)`. No I/O, no globals — these are the unit-tested core.
- Computed per symbol per scan; results persisted with the scan record.

### FR-4 — Change detection
- After each scan, build `current_set` = symbols where `in_range == true`.
- Compare with the previous scan's set (of any type — `PRE`, `OPEN`, `CLOSE`, or manual).
- If `current_set == previous_set` → **no message sent**, scan still persisted.
- If different → send digest, highlighting entries (`+`) and exits (`−`).
- The first scan of each trading day sends the full list regardless of change, so there is a daily baseline even on quiet days (§14, Q1).

### FR-5 — Telegram delivery
Message format:

```
📊 MA150 Screener — OPEN scan
Fri 07 Aug, 09:45 ET

In range (|P − MA150| ≤ 1.5·ATR):
  AAPL    +0.21 ATR   $198.40  (MA 196.9)
  KO      −0.44 ATR   $ 61.02  (MA  62.1)
  NVDA    +1.38 ATR   $122.80  (MA 110.4)

Entered: NVDA
Exited:  MSFT

12 in range · 148 scanned
⚠️ No data: PLTR, SOFI · Insufficient history: ARM
```

- Sorted by `abs(distance)` ascending — closest to the MA first.
- The `⚠️` footer appears in **every** message whenever any symbol is in a non-OK state, not only when that state changes (§14, Q3). If all symbols are healthy the line is omitted entirely.
- A `~` prefix on a price means it is a fallback previous close rather than a live quote (§14, Q2).
- Split messages at 4096 characters (Telegram hard limit).
- Delivery failures retried 3×, then logged; a failed alert must never crash the scheduler.

### FR-6 — Persistence (logical model)

The logical model below is backend-agnostic. Physical DynamoDB key design is in §8.3; the analytical SQLite copy in §8.4 materialises exactly this schema.

```sql
symbols(symbol PK, added_at, active, last_validated_at)

daily_bars(symbol, date, open, high, low, close, volume,
           PRIMARY KEY (symbol, date))

scans(id PK, scan_type, scheduled_at, ran_at, status,
      symbols_scanned, in_range_count, error_count, notes)

scan_results(scan_id FK, symbol, price, sma150, atr14,
             distance_atr, in_range, status,
             PRIMARY KEY (scan_id, symbol))

alerts(id PK, scan_id FK, sent_at, message, delivery_status)
```

#### Growth and retention

Two tables append forever, one updates in place. Nothing is deleted.

| Table | Behaviour | Rows/year @150 symbols | Size/year |
|---|---|---|---|
| `symbols` | **Update in place.** ~150 rows, flat. | — | ~15 KB total |
| `daily_bars` | **Append**, one row per symbol per trading day. Upsert on `(symbol, date)` so late corrections and split re-fetches overwrite rather than duplicate. | 150 × 252 ≈ 38k | ~5 MB |
| `scans` | Append, immutable. | 3 × 252 ≈ 760 | ~0.1 MB |
| `scan_results` | Append, immutable. The bulk of the database. | 150 × 3 × 252 ≈ 113k | ~14 MB |
| `alerts` | Append, immutable; stores full message text. | ~500 | ~0.5 MB |

**≈ 20 MB per year, ~200 MB after a decade.** SQLite handles multi-GB databases comfortably, so there is no rollup, no partitioning, and no retention window. Full history is kept permanently — Phase 2 depends on it, and discarding it would mean the back-tester can only re-derive signals from adjusted prices rather than replaying what was actually observed at the time.

Initial backfill (2 years of bars for 150 symbols) is ~75k rows, ~8 MB, one-time. Adding a symbol later backfills ~500 rows.

The DB file is small enough that backup is a plain nightly file copy — no dump tooling needed. Run `VACUUM` quarterly, purely for cleanliness.

### FR-7 — Observability
- Structured logging to file with rotation.
- Daily heartbeat appended to the `CLOSE` message (or a standalone "all quiet" ping if nothing changed for 3 consecutive days).
- System-error alerts (data outage, provider auth failure, scheduler miss) go to the same chat, prefixed `⚠️ SYSTEM`.

---

## 7. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Runtime | A 200-symbol scan completes in < 60s. |
| Cost | $0/month on Lambda free tier; $0 marginal on RPi5. |
| Reliability | Missed scan is detected and reported; the next scan runs normally. |
| Secrets | Bot token and chat ID from SSM Parameter Store (Lambda) or `.env` (RPi) — never committed. |
| Portability | The same container image runs on Lambda and on the RPi5. Moving between them changes configuration only: connection string, scheduler, and Telegram transport. No application code changes. |
| Testability | Indicator math covered by unit tests with hand-checked fixtures; scan pipeline covered by integration tests against recorded fixture data (no network in CI). |

---

## 8. Deployment

**Target: AWS Lambda now, Raspberry Pi 5 later.** The RPi5 is the better long-term home — always-on, local SQLite, zero marginal cost, no cold starts. Lambda is the low-commitment start. The design goal is therefore that **the migration is a config change, not a rewrite.**

### 8.1 Lambda topology

| Concern | Implementation |
|---|---|
| Scheduled scans | EventBridge Scheduler → `scan` Lambda, three rules (`PRE`, `OPEN`, `CLOSE`). Supports cron in a named timezone, so `America/New_York` is native and DST is handled for you. |
| Telegram commands | **Webhook, not long-polling.** A Lambda Function URL is registered via `setWebhook`; Telegram POSTs each update. No API Gateway needed, no cost. |
| Packaging | Container image Lambda. `pandas` + `numpy` + `yfinance` exceed the 250 MB zip limit comfortably; the 10 GB image limit does not care. |
| Secrets | Bot token in SSM Parameter Store (SecureString), read at cold start. |
| Concurrency | Reserved concurrency = 1 on the scan function. Prevents two scans racing on the same DB. |

Cold start with pandas is 2–5s. Irrelevant for cron; slightly noticeable on a `/list` command, acceptable.

Cost: comfortably inside the perpetual free tier. ~1,000 invocations/month against 1M free. Effectively $0.

### 8.2 Database choice

Lambda has an ephemeral filesystem, so a local SQLite file cannot survive between invocations.

Workload for sizing: **~12,500 rows written per month**, ~800 read-units per day, 20 MB of storage per year. This is orders of magnitude below every free tier available, so **cost does not separate the candidates — all of them are $0.** The decision rests on operational risk and on the Phase 2/3 story.

| Option | Cost at our volume | Real trade-off |
|---|---|---|
| **DynamoDB (provisioned)** | **$0** — 25 GB, 25 WCU, 25 RCU always-free; we use ~0.04% | ✅ **Chosen.** No concurrency machinery. No SQL. |
| S3 + SQLite file | ~$0.01/mo | Requires hand-rolled ETag compare-and-swap; whole DB downloaded per invoke |
| Turso / libSQL | $0 (5 GB, 10M writes/mo) | Third-party dependency; free tier already tightened once |
| Cloudflare D1 | $0 (5 GB, 100k writes/day) | Bills rows *scanned*; HTTP API second-class vs Workers binding |
| EFS + Lambda | **~$32/mo** (NAT Gateway) | Rejected on cost |

**Decision: DynamoDB, provisioned capacity mode.**

This reverses the earlier draft. The original objection — that `scan_results` queries for Phase 2/3 fight the key-value model — is real but solvable (§8.4), whereas the objection to S3+SQLite is *not* solvable: it requires writing and correctly testing an optimistic-locking protocol for a hobby project. Concurrency bugs are the kind that surface as silently missing data six months later. DynamoDB makes that entire class of problem disappear.

> ⚠️ **The free tier applies to provisioned capacity mode only.** On-demand tables are billed per request from the very first write, with no free allowance. This is the single most common DynamoDB billing surprise. Provision the table at 25 RCU / 25 WCU, disable auto-scaling (or cap it at 25), and set a billing alarm at $1.

### 8.3 Physical data model — single table

One table, `screener`, keeps all entities in the shared 25/25 capacity pool. Attribute names are deliberately short: DynamoDB stores attribute names on every item.

| Entity | PK | SK | Attributes |
|---|---|---|---|
| Universe member | `UNIVERSE` | `SYM#AAPL` | `added`, `active`, `validated` |
| Daily bar | `BAR#AAPL` | `2026-08-07` | `o`,`h`,`l`,`c`,`v` |
| **Indicator cache** | `IND#AAPL` | `2026-08-07` | `sma`, `atr`, `asof` |
| Scan summary | `SCAN` | `2026-08-07T09:45Z#OPEN` | `status`, `n`, `inrange[]`, `errs[]` |
| Observation | `HIST#AAPL` | `2026-08-07T09:45Z` | `p`, `sma`, `atr`, `dist`, `in` |
| Paper trade (Ph2) | `TRADE#AAPL` | `<opened_at>` | … |

Access patterns, all served without a GSI:

| Need | Operation |
|---|---|
| Current universe | `Query PK=UNIVERSE` |
| Bars for indicator calc | `Query PK=BAR#<sym>, SK ≥ <date-1y>` |
| Previous in-range set | `Query PK=SCAN, Limit 1, ScanIndexForward=false` |
| Full history for one symbol (Phase 2) | `Query PK=HIST#<sym>` |
| Trades for one symbol | `Query PK=TRADE#<sym>` |

Note that change detection reads **one item** — the previous scan summary carries its `inrange` list inline. No diffing query, no table scan.

#### The indicator cache earns its place

§4.3 already established that SMA150 and ATR14 only change when a daily bar completes. So they are computed **once per day, at the `CLOSE` scan**, and written to `IND#<symbol>`. The `PRE` and `OPEN` scans then read 150 tiny items instead of 150 × 165 bars — dropping those scans from ~255 RCU to ~10 RCU, and cutting their runtime substantially. This is worth doing regardless of the storage backend.

#### Capacity check

| | Per scan | Per day | Free tier/day | Utilisation |
|---|---|---|---|---|
| Reads | ~255 RCU (`CLOSE`), ~10 RCU (`PRE`/`OPEN`) | ~275 RCU | 2,160,000 | **0.01%** |
| Writes | ~301 WCU (`CLOSE`), ~151 (`PRE`/`OPEN`) | ~600 WCU | 2,160,000 | **0.03%** |

Bursts are absorbed by DynamoDB's accumulated burst capacity (up to 300 s of unused throughput), so the `CLOSE` scan's 300-item write completes in about a second despite the 25 WCU/s steady-state limit. Use `BatchWriteItem` (25 items per call).

Storage: ~15 MB/year against a 25 GB allowance — roughly 1,600 years of headroom.

### 8.4 Closing the analytics gap

DynamoDB has no SQL, no joins, no aggregates. Phase 2's back-tester and Phase 3's dashboard both want exactly those.

**Resolution: nightly export to a local SQLite file.** After the `CLOSE` scan, dump the table to `screener.db` and push it to S3 (a few MB, pennies). Phase 2 and Phase 3 read *that* file, with full SQL, joins, window functions, and pandas. DynamoDB remains the operational store; SQLite becomes the analytical copy.

This is the standard operational/analytical split, and it turns out to hand us two things for free:
- The dashboard can never take down or slow the screener — it touches a different artefact entirely.
- Ad-hoc research ("every time a symbol sat within 0.5 ATR, what happened over the next 20 days?") is a SQL query against a local file, not a DynamoDB access-pattern redesign.

### 8.5 What changes on the move to RPi5 (data)

Be clear-eyed about this: **DynamoDB does not run on a Raspberry Pi.** Two honest paths:

1. **Keep DynamoDB, move only the compute.** The Pi calls the AWS API over HTTPS. Still free, no data migration, works immediately. But your home-hosted system retains a cloud dependency — which may defeat the point of moving home.
2. **Migrate to local SQLite.** The nightly export from §8.4 *is* the migration artefact — the SQLite file already exists and is already current. Swap the repository implementation from the DynamoDB one to the SQLite one and point it at the local file.

Path 2 costs one repository implementation, written against an interface that already exists. That is the price of this decision, and it is worth paying to avoid hand-rolled write-conflict handling now.

Everything else on the move: EventBridge Scheduler → APScheduler in a long-running process; Telegram webhook → long-polling (no public endpoint needed at all); same container image rebuilt for `linux/arm64`.

**Requirement:** all database access goes through the repository interface (§9). No DynamoDB calls outside it. This is what makes path 2 a contained change rather than a rewrite.

### 8.6 What changes on the move to RPi5 (compute)

Almost nothing else, by design:

- EventBridge Scheduler → APScheduler inside a single long-running process (or host cron).
- Telegram webhook → long-polling, which removes the need for a public endpoint entirely.
- Container image → the same image under Docker Compose, `linux/arm64` build.

Two RPi-specific notes: put the database on SSD/NVMe rather than the SD card, and accept that alerts now depend on your home power and internet. A UPS and a healthcheck ping to an external uptime monitor cover most of that.

### 8.7 Rejected: GitHub Actions

Worth recording. Telegram command handling needs something *listening*; a cron-triggered job is not. State would have to be committed back to the repo on every run. And Actions' scheduled triggers are best-effort, routinely drifting 5–15 minutes — unacceptable for the `OPEN` scan.

---

## 9. Architecture

```
┌──────────────────────────────────────────────┐
│  Application (single Python process)         │
│                                              │
│  ┌────────────┐   ┌──────────────────────┐   │
│  │ Scheduler  │──▶│  Scan Pipeline       │   │
│  │ APScheduler│   │  fetch → indicators  │   │
│  │ + XNYS cal │   │  → evaluate → diff   │   │
│  └────────────┘   └──────────┬───────────┘   │
│                              │               │
│  ┌────────────┐              ▼               │
│  │ Telegram   │◀────┬────────────────┐       │
│  │ Bot (poll) │     │  Notifier      │       │
│  └─────┬──────┘     └────────────────┘       │
│        │                     ▲               │
│        ▼                     │               │
│  ┌──────────────────────────────────────┐    │
│  │  Repository layer  →  SQLite         │    │
│  └──────────────────────────────────────┘    │
│                     ▲                        │
│  ┌──────────────────┴───────────────────┐    │
│  │ MarketDataProvider (yfinance impl)   │    │
│  └──────────────────────────────────────┘    │
└──────────────────────────────────────────────┘
```

**Layering rule:** the indicator module and the criterion evaluator must have no knowledge of Telegram, SQLite, or yfinance. Phase 2's back-tester will re-use them directly against historical bars.

Suggested layout:

```
src/
  providers/      market data interface + yfinance impl
  indicators/     sma.py, atr.py  (pure)
  screener/       criterion.py, pipeline.py, diff.py
  storage/        models.py, repository.py, migrations/
  notify/         telegram_client.py, formatters.py
  bot/            commands.py
  scheduler/      calendar.py, jobs.py
  config.py
tests/
```

---

## 10. Extensibility: adding indicator #2

The criterion is defined as a composable predicate so the next indicator (RSI, volume surge, MA slope, whatever) drops in without rework:

```python
class Criterion(Protocol):
    name: str
    def evaluate(self, bars: DataFrame, price: float) -> CriterionResult: ...
```

A scan runs all registered criteria and stores each result; the alert filter is a configurable combination (`ALL`, `ANY`, or a named expression). Phase 1 registers exactly one criterion and uses `ALL`. `scan_results` gains a `criterion` column when the second one lands.

---

## 11. Phase 2 Design Sketch — Paper Trading

Purpose: measure whether "price returns to the MA150 band" is actually tradable, with real recorded signals rather than hindsight.

- **Entry rule (to be defined by you):** the natural default is enter on the scan where a symbol *enters* the band; direction determined by which side it entered from.
- **Exit rules:** target in ATR multiples, stop in ATR multiples, and a max-holding-period bar count. All three configurable.
- **Position sizing:** fixed fractional risk (e.g. 1% of a notional $100k account per trade, size = risk / stop distance).
- **Fills:** next scan's reference price, with a configurable slippage assumption in basis points. No intra-bar fills — the data doesn't support that honestly.
- **Tables:** `paper_positions`, `paper_trades`, `equity_curve`.
- **Metrics:** win rate, average R multiple, expectancy, profit factor, max drawdown, average holding period, per-symbol breakdown, distribution of outcomes by entry distance.
- **Back-test mode:** the same engine runs over historical bars to produce a baseline before any forward paper trading accumulates.

The one design decision that matters now: `scan_results` must store the *inputs* (price, sma, atr) and not just the boolean, so the Phase 2 engine can reconstruct any rule variant from recorded history without refetching.

---

## 12. Phase 3 Design Sketch — Dashboard

Read-only web UI over the same SQLite database. No writes, no auth beyond a single shared token or Tailscale-only access.

**Pages**
1. **Overview** — current in-range table (symbol, distance, price, MA, ATR, days-in-range), last scan status, universe size, system health.
2. **Symbol detail** — price chart with MA150 and the ±1.5 ATR band overlaid, markers for historical in-range entries/exits, that symbol's paper-trade log.
3. **Performance** — equity curve, the metric set from §11, filterable by date range and symbol.
4. **History** — searchable scan log with per-scan results, for auditing "why didn't I get an alert on X".

**Stack:** FastAPI serving a small React SPA, or Streamlit if speed of build beats polish. Charts via Lightweight Charts (TradingView's OSS library) for price panes, Recharts for metrics.

**API surface:** `GET /api/scans/latest`, `GET /api/symbols/{symbol}/history`, `GET /api/performance`, `GET /api/health`.

---

## 13. Cost Model

Assumptions: 21 trading days/month, 63 scheduled scans, 150 symbols, ~50 Telegram commands/month, `us-east-1`, Lambda at 1024 MB.

Estimated runtime per scan: ~40 s for `CLOSE` (full bar fetch + indicator recompute), ~15 s for `PRE` and `OPEN` (indicator cache hit, price fetch only). Total ≈ 1,470 GB-seconds/month.

| Line item | Basis | Monthly |
|---|---|---|
| Lambda compute — scans | 1,470 GB-s of a 400,000 GB-s always-free allowance (0.37%) | $0.00 |
| Lambda compute — webhook | ~75 GB-s | $0.00 |
| Lambda requests | ~115 of 1,000,000 free | $0.00 |
| Lambda Function URL | No API Gateway needed | $0.00 |
| EventBridge Scheduler | 63 invocations @ $1.00/million | $0.0001 |
| DynamoDB capacity | 5 RCU / 5 WCU provisioned, inside the 25/25 always-free tier | $0.00 |
| DynamoDB storage | ~15 MB of 25 GB free | $0.00 |
| **ECR image storage** | **2 images × ~500 MB @ $0.10/GB-month** | **$0.10** |
| S3 — analytical exports | ~0.5 GB @ $0.023/GB (7 daily + 12 monthly snapshots) | $0.01 |
| S3 — requests | ~150 PUT/GET | $0.001 |
| CloudWatch Logs | ~2 MB ingest of 5 GB free; 14-day retention | $0.00 |
| SSM Parameter Store | Standard parameters | $0.00 |
| Data transfer out | A few MB; first 100 GB/month free | $0.00 |
| **Total** | | **≈ $0.11 / month** |

**Budget: under $0.25/month.** Set a billing alarm at $1 — not because the forecast is tight, but because every plausible failure mode (a runaway retry loop, an accidental on-demand table, a log-level left at DEBUG) shows up as a billing anomaly first.

### 13.1 The one line item that isn't free

**ECR container image storage is the largest cost in the system** — roughly 90% of the total. Lambda container images live in ECR at $0.10/GB-month, and a `pandas` + `numpy` + `yfinance` image is 400–600 MB.

Mitigations, in order of effort:
- Set an ECR lifecycle policy retaining the 2 most recent images. Without one, every deploy accumulates forever and this line item grows without limit.
- Slim the image: `--no-compile`, strip tests and `__pycache__`, drop `matplotlib`/`scipy` if they arrive as transitive dependencies. ~250 MB is achievable.
- Or abandon container packaging entirely and use a zip deployment with AWS's managed SciPy layer, which takes ECR to $0. More fiddly to reproduce locally; only worth it if you object to the $0.10 on principle.

### 13.2 Capacity provisioning — hedging the free tier

Provision **5 RCU / 5 WCU, not 25/25**, even though 25/25 is free.

DynamoDB accumulates 300 seconds of unused throughput as burst capacity. At 5 WCU that is 1,500 WCU of burst against a peak requirement of ~301 (the `CLOSE` scan's batch write), and 1,500 RCU against a peak of ~255. Comfortable margin.

The reason not to take the full 25/25: it costs the same today ($0), but if the always-free tier is ever withdrawn or altered, exposure at 25/25 is **$14.23/month** versus **$2.85/month** at 5/5. Same behaviour, one-fifth the downside.

### 13.3 If every free tier disappeared tomorrow

| Component | Cost |
|---|---|
| DynamoDB @ 5/5 provisioned | $2.85 |
| Lambda compute + requests | $0.03 |
| ECR, S3, EventBridge, logs | $0.15 |
| **Worst-case total** | **≈ $3.03 / month** |

Worth knowing, because it means the project has no realistic path to an unpleasant bill.

### 13.4 What this does *not* cover

- **Market data.** yfinance is free and unofficial. If it degrades and you move to a paid feed, expect $30–$100/month, which would then dominate every other cost in this table by two orders of magnitude. This is the real financial risk in the project, and it is a data risk, not an infrastructure one.
- **Phase 3 dashboard hosting.** Reading a static SQLite export, so a static host or the RPi itself covers it at $0.
- **Domain name**, if you ever want one for the dashboard: ~$12/year.

---

## 14. Resolved Decisions

All previously open questions are now closed. Recorded here because each one is a behaviour someone will later ask "why does it do that?" about.

| # | Question | Decision |
|---|---|---|
| Q1 | Daily baseline: should the first scan of each day always send, even if nothing changed? | **Yes.** The first scan of each trading day sends the full in-range list unconditionally. Silence then unambiguously means "the system ran and nothing changed" rather than "the system is dead". |
| Q2 | Pre-market reference price when a symbol has no pre-market trades. | **Fall back to the previous close, and mark it** with a `~` prefix in the message so a stale price is never mistaken for a live one. |
| Q3 | Should `INSUFFICIENT_DATA` / `DATA_ERROR` symbols appear in every message or only on change? | **Every message.** A persistent data failure that stops being mentioned is a failure you stop noticing. Listed compactly in a footer line. |
| Q4 | Is the band width (1.5) per-symbol or global? | **Global, configurable** via `BAND_ATR_MULT`. Per-symbol tuning is a Phase 2 question, once there is performance data to tune against. |
| Q5 | Retention: keep every scan forever, or roll up? | **Forever.** ~15 MB/year against a 25 GB free allowance. Phase 2 depends on the full observation record. |

---

## 15. Milestones (Phase 1)

| # | Deliverable | Definition of done |
|---|---|---|
| M1 | Indicator core | `sma`/`atr` pass unit tests against hand-checked fixtures. |
| M2 | Data layer | 150 symbols fetched + cached; delta-refresh works; split detection works. |
| M3 | Screener pipeline | End-to-end scan produces persisted `scan_results` from a CLI invocation. |
| M4 | Telegram out | Digest formatting + change detection verified against a scripted 3-scan sequence. |
| M5 | Telegram in | `/add`, `/remove`, `/list`, `/status`, `/scan` working; unauthorised chats ignored. |
| M6 | Scheduling + calendar | Correct behaviour across a weekend, a holiday, and a half-day (tested with a frozen clock). |
| M7 | Deploy | Docker Compose on VPS, secrets externalised, nightly DB backup, 5 consecutive clean trading days. |
