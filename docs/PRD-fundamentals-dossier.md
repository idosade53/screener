# PRD — Fundamentals & News Dossier (Phase 4)

**Version:** 0.1 (draft)
**Date:** 2026-08-08
**Owner:** [you]
**Status:** Approved — implementation pending (milestones §14)

---

## 1. Summary

An on-demand tool that produces a **fundamentals + news dossier** for one US-listed symbol. Triggered by `/dossier TSLA` in Telegram or `screener dossier TSLA` on the CLI, it fetches a company profile, standardized financial metrics, analyst view, next-earnings timing, and recent news; scores them against configurable rules into a green/yellow/red **scorecard**; and returns a single formatted report. An optional stage summarizes the news + fundamentals into a one-paragraph plain-English read via Claude Haiku.

**Why this phase.** Phase 1 (M0–M7) tells you *when* a symbol you already like is trading within 1.5×ATR of its MA150 — mechanically "in a good position to buy." It says nothing about *whether the company is worth buying*. Today, when an alert fires, you leave Telegram and manually check fundamentals and news before committing capital. This phase closes that gap. Because fundamentals change only quarterly, aggressive per-symbol caching keeps external calls to a trickle well inside free tiers.

It reuses Phase 1's hexagonal architecture wholesale: new **ports** (`FundamentalsProvider`, `NewsProvider`, optional `SummaryProvider`) with **adapters** for Financial Modeling Prep (fundamentals), Finnhub (news + earnings), and yfinance (zero-key fallback). Snapshots are cached in the existing repository so a repeat lookup the same day costs zero external calls.

This is deliberately sequenced *before* the originally-sketched Phase 2 (paper trading) and Phase 3 (dashboard) in `docs/PRD-stock-screener.md` §11–12; those remain designed-but-unbuilt.

**Explicit non-recommendation:** the dossier surfaces flags and facts; it never emits a buy/sell/hold recommendation. It is decision *support*, not a decision.

---

## 2. Goals & Non-Goals

### Goals (Phase 4)
- G1. Produce a complete single-symbol fundamentals + news dossier on demand, in < ~10 s warm / < ~30 s cold.
- G2. Reduce the fundamentals research to a scannable scorecard: valuation, growth, profitability, balance-sheet health, analyst view, earnings timing — each flagged green/yellow/red with the underlying number.
- G3. Surface the last N days of company-specific news (headline, source, date, link).
- G4. Stay free or near-free: $0/month for the structured core at realistic on-demand volume; opt-in AI summary at fractions of a cent per dossier.
- G5. Keep every external feed behind a narrow port with a zero-key fallback, so any single provider can be swapped or can fail without taking the feature down.

### Non-Goals (Phase 4)
- Any buy/sell/hold recommendation, target price of our own, or portfolio/position sizing.
- Screening or ranking *across* symbols on fundamentals (this is per-symbol, on demand). A fundamentals filter in the nightly scan is a later idea (§13).
- Auto-attaching fundamentals to scan alerts (considered and declined for now — §12 D2).
- Non-US exchanges; real-time intraday fundamentals; historical fundamentals back-testing.
- Storing full financial statements. We persist the derived metrics we score on, not raw 10-K line items.

### Success criteria
- SC-1. `screener dossier AAPL` and `/dossier AAPL` return a correctly formatted dossier for a large-cap in a single invocation.
- SC-2. A second dossier for the same symbol within the cache window makes **zero** external fundamentals/news calls (verifiable in logs).
- SC-3. Scorecard verdicts match hand-computed expectations on a fixtured 5-symbol audit (mega-cap, value, high-growth-unprofitable, high-debt, insufficient-data).
- SC-4. A single provider outage (FMP or Finnhub) degrades gracefully to the fallback / a partial dossier with a clear note — it never raises to the user.
- SC-5. Turning the AI summary off (default) makes zero LLM calls; turning it on adds exactly one.

---

## 3. Users & Context

Same single user as Phase 1 — technically capable, Israel (UTC+3), trading US equities, living in Telegram. The workflow: a scan alert says "TSLA is in the band"; the user replies `/dossier TSLA` to sanity-check the company before buying. The dossier must be readable on a phone at a glance, so the scorecard leads and the raw data follows.

---

## 4. The Dossier

### 4.1 Sections (in report order)

| # | Section | Content |
|---|---|---|
| 1 | Header | Symbol, company name, sector/industry, market cap, current price + distance-to-MA150 (reused from the last scan if available). |
| 2 | **Scorecard** | The six flag lines from §4.2 — the actionable core. |
| 3 | Valuation | P/E (ttm & fwd), P/S, PEG, EV/EBITDA, P/B. |
| 4 | Growth | Revenue YoY, EPS YoY, (3-yr revenue CAGR if available). |
| 5 | Profitability | Gross / operating / net margin, ROE, free-cash-flow positive? |
| 6 | Balance sheet | Debt/Equity, current ratio, net-debt/EBITDA, interest coverage. |
| 7 | Analyst view | Consensus rating, # analysts, mean target vs current price (implied %). |
| 8 | Earnings timing | Next earnings date (+ "in N days" flag), last earnings surprise %. |
| 9 | News | Up to `news_max_items` headlines from the last `news_lookback_days` days: date · source · title · link. |
| 10 | AI read *(optional)* | One-paragraph Claude-written synthesis of §7–9 + the scorecard. Off by default. |
| 11 | Footer | Data sources + fetch timestamps per section; any `DATA_ERROR`/`STALE` notes. |

Missing metrics render as `n/a` and down-weight (never fail) the corresponding scorecard line.

### 4.2 Scorecard rules (pure, deterministic)

Six categories, each → `GREEN` / `YELLOW` / `RED` / `NA`, with the driving value and a one-line note. Thresholds are **global and configurable** (a `ScorecardThresholds` config object), not hard-coded — sector-relative tuning is deferred (§13). Illustrative defaults:

| Category | GREEN | YELLOW | RED |
|---|---|---|---|
| Valuation | P/E < 20 and PEG < 1.5 | P/E 20–40 | P/E > 40 or negative earnings |
| Growth | rev YoY > 15% | 0–15% | negative |
| Profitability | net margin > 10% and FCF+ | thin but positive | negative net margin |
| Balance sheet | D/E < 0.5 and current ratio > 1.5 | moderate leverage | D/E > 2 or current ratio < 1 |
| Analyst view | mean target > +15% and rating ≥ Buy | mixed | target below price |
| Earnings timing | > 10 trading days out | 3–10 days | ≤ 2 days (caution: don't buy into the print) |

Output also carries a headline **tally** (e.g. "4🟢 1🟡 1🔴") — a summary of flags, **not** a score-to-action mapping.

### 4.3 Data freshness & caching (critical rule)

Fundamentals only change on an earnings release. So a `FundamentalsSnapshot` is cached per symbol and considered fresh until **the later of** (a) `fetched_at + fundamentals_cache_days` (default 1 trading day) or (b) it is refetched once `next_earnings_date` has passed. News is cached with a short TTL (`news_cache_hours`, default 6 h). This is what keeps the feature inside free tiers: a burst of dossiers on the same watchlist reuses cached data.

---

## 5. Triggers & Commands

On-demand only (no auto-teaser — §12 D2). Two surfaces, one formatter:

- **Telegram** — `/dossier <SYMBOL>` (alias `/dd`). Authorized chat only (reuses `bot/auth.py::is_authorized`). Unknown/invalid symbol → validated via the provider, friendly rejection. Long reports use the existing 4096-char chunking in `adapters/notify/telegram_notifier.py`.
- **CLI** — `screener dossier <SYMBOL> [--no-cache] [--ai]`, added to `composition/cli.py`. `--no-cache` forces a refetch; `--ai` forces the summary on for that run.

---

## 6. Functional Requirements

### FR-1 — New ports (interfaces only, in `ports/`)
- `FundamentalsProvider`: `fetch_profile(symbol) -> CompanyProfile`, `fetch_fundamentals(symbol) -> FundamentalsSnapshot`, `validate_symbol(symbol) -> bool`.
- `NewsProvider`: `fetch_company_news(symbol, since: date) -> list[NewsItem]`.
- `SummaryProvider` *(optional stage)*: `summarize(dossier_context) -> str`.
- Mirrors the existing `ports/market_data.py` Protocol style; adapters depend inward only (enforced by `.importlinter`).

### FR-2 — Fundamentals ingestion (adapters)
- Primary `FmpFundamentalsProvider` (`adapters/fundamentals/fmp_provider.py`): calls FMP `profile`, `ratios-ttm`, `key-metrics-ttm`, `income-statement` (annual, 2 periods for YoY), `analyst estimates / price-target-consensus`. Normalizes to `Decimal` at the boundary (project numeric policy), quantized to 4 dp.
- Fallback `YFinanceFundamentalsProvider` (`adapters/fundamentals/yfinance_provider.py`): reuses the yfinance dependency (`.info`, `.financials`, `.balance_sheet`, `.cashflow`, `.recommendations`, `.calendar`). Used when FMP fails or returns nothing.
- Retry/backoff via the same `_with_retries` pattern already in `adapters/market_data/yfinance_provider.py`; injectable network seam (`http_get_fn`) so contract tests replay recorded JSON with no network.

### FR-3 — News ingestion (adapter)
- Primary `FinnhubNewsProvider` (`adapters/news/finnhub_provider.py`): `company-news?symbol=&from=&to=`, mapped to `NewsItem` (datetime, source, headline, url, optional summary). Dedup + sort desc, cap at `news_max_items`.
- Fallback: yfinance `.news`.

### FR-4 — Scorecard engine (pure)
- `screener/fundamentals/scorecard.py::score(snapshot, thresholds) -> Scorecard`. No provider/telegram/db knowledge — same layering rule as `screener/criterion.py`, directly unit-testable and reusable by any future surface.

### FR-5 — Dossier assembly + delivery
- `screener/fundamentals/dossier.py::build_dossier(symbol, ...)` orchestrates: cache check → providers → scorecard → optional summary → `Dossier` value object.
- `screener/fundamentals/formatters.py::format_dossier(dossier) -> str` (Telegram/CLI text), styled after `screener/formatters.py`.
- Bot command in `bot/commands.py` + `bot/dispatch.py`; CLI subcommand in `composition/cli.py`; all wiring in `composition/wiring.py::build_application` (the only place adapters are constructed).

### FR-6 — Caching & persistence
- New repository-port methods: `get_fundamentals_snapshot(symbol)` / `put_fundamentals_snapshot(...)` / `get_news_cache(symbol)` / `put_news_cache(...)`.
- SQLite: new tables `fundamentals_snapshot`, `news_cache` (`adapters/repository/sqlite_repository.py`, documented in `docs/db-schema.md`).
- DynamoDB: new single-table items `PK=FUND#<symbol>` and `PK=NEWS#<symbol>` (`adapters/repository/dynamodb_repository.py`).
- Both must pass the **shared repository contract suite** (`tests/contract/repository_contract.py`) — add the new cases there so SQLite and DynamoDB stay behaviourally identical.

### FR-7 — Optional AI summary
- `AnthropicSummaryProvider` (`adapters/summary/anthropic_provider.py`) using Claude **Haiku 4.5** (`claude-haiku-4-5-20251001`) via the Messages API. Prompt is fed only the already-fetched structured data + headlines (no extra data fetch). Gated by `dossier_ai_summary` (default `false`); `--ai` overrides per-call. Fully mocked in tests behind the `SummaryProvider` port — no network in CI.

---

## 7. Non-Functional Requirements

| Area | Requirement |
|---|---|
| Cost | Structured core $0/month at on-demand volume; AI summary opt-in, ≈ $0.001–0.005/dossier. |
| Rate limits | Stay within FMP free (~250 calls/day) and Finnhub free (60/min) — caching + on-demand-only makes this trivial; a per-provider daily call budget is logged. |
| Resilience | No provider error ever reaches the user; degrade to fallback or a partial dossier with a footer note. Providers never raise past the adapter (mirrors `telegram_notifier`). |
| Numeric policy | All money/ratio values `Decimal`, quantized to 4 dp at the boundary; `float` only where pandas requires it. |
| Layering | `import-linter` contract extended to the new packages; `mypy --strict` clean; ruff clean. |
| Secrets | `FMP_API_KEY`, `FINNHUB_API_KEY`, optional `ANTHROPIC_API_KEY` via `.env` locally and SSM on Lambda (`composition/secrets.py`); never logged. |
| No network in tests | Provider HTTP seams injected; recorded JSON frames replayed; LLM mocked. |

---

## 8. Data providers & fallback

| Concern | Primary | Fallback | Why |
|---|---|---|---|
| Fundamentals / ratios / analyst | **Financial Modeling Prep** (free) ✅ | yfinance | Purpose-built, standardized statements + ratios; ~250 calls/day covers on-demand use with caching. |
| Company news + earnings calendar | **Finnhub** (free) ✅ | yfinance `.news` | Free `company-news` is reliable and dated; 60 calls/min is ample. |
| AI summary (optional) | **Claude Haiku 4.5** ✅ | — | Cheapest capable model; a few-hundred-token summary is a fraction of a cent. |

> ⚠️ **The whole point of the ports is that this table can change without touching the scorecard, formatter, or bot.** FMP/Finnhub free tiers are the current best free option; if either degrades, swap the adapter, not the feature. This mirrors Phase 1's design driver on `yfinance` being "the single most likely component to be replaced."

---

## 9. Architecture

Additive to the Phase 1 hexagon — no changes to `domain`/`indicators`, the screener pipeline, or existing adapters.

```
src/screener/
  ports/
    fundamentals.py     # NEW  FundamentalsProvider Protocol
    news.py             # NEW  NewsProvider Protocol
    summary.py          # NEW  SummaryProvider Protocol (optional stage)
  adapters/
    fundamentals/       # NEW  fmp_provider.py, yfinance_provider.py
    news/               # NEW  finnhub_provider.py
    summary/            # NEW  anthropic_provider.py
    repository/         #   +  fundamentals_snapshot / news_cache methods
  screener/fundamentals/  # NEW  scorecard.py, dossier.py, formatters.py, thresholds.py
  domain/models.py      #   +  CompanyProfile, FundamentalsSnapshot, NewsItem,
                        #        ScoreLine, Scorecard, Dossier (frozen dataclasses)
  bot/                  #   +  /dossier command + dispatch
  composition/          #   +  wiring + cli subcommand
  config.py             #   +  fundamentals/news/AI settings
```

**Layering rule (unchanged):** `scorecard.py` and `formatters.py` know nothing of providers, telegram, or the DB — the same discipline that lets the Phase 1 indicators be reused by the future back-tester.

---

## 10. Persistence schema (additions)

| Store | Fundamentals | News |
|---|---|---|
| SQLite | table `fundamentals_snapshot(symbol PK, fetched_at, next_earnings_date, payload_json, source)` | table `news_cache(symbol PK, fetched_at, payload_json, source)` |
| DynamoDB | `PK=FUND#<symbol>, SK=SNAPSHOT` | `PK=NEWS#<symbol>, SK=LATEST` |

Metrics are stored as a compact JSON payload of the derived numbers we score on (not raw statements). Retention: keep-latest-only per symbol (unlike scan history, there's no analytical value in old snapshots — they're a cache). Storage impact is negligible against the 25 GB free allowance.

---

## 11. Cost model

Assumptions: on-demand use, ~10–50 dossiers/day, caching per §4.3.

| Line item | Basis | Monthly |
|---|---|---|
| FMP fundamentals | free tier, ~4 calls/cold-dossier, cached | $0.00 |
| Finnhub news + earnings | free tier, ~2 calls/cold-dossier, cached | $0.00 |
| yfinance fallback | free/unofficial | $0.00 |
| Repository storage | latest-only snapshots, few MB | $0.00 |
| Claude Haiku (opt-in) | ~50 dossiers × ~700 tok, only if `--ai`/enabled | **≈ $0.05–0.25** |
| **Total (AI off)** | | **$0.00** |
| **Total (AI on)** | | **< $0.25** |

Consistent with Phase 1's "no realistic path to an unpleasant bill." The only non-free line is fully opt-in.

---

## 12. Resolved Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Data-provider strategy | **FMP (fundamentals) + Finnhub (news/earnings), yfinance fallback**, all behind ports. Two free keys; best data quality with a zero-key safety net. |
| D2 | Trigger model | **On-demand only** (`/dossier`, `screener dossier`). No auto-teaser on band-entry for now — keeps calls minimal and the user in control; revisit as §13. |
| D3 | AI summary | **Structured-first; AI is an optional, off-by-default stage** behind `SummaryProvider` (Claude Haiku). Core stays free and deterministic. |
| D4 | Delivery surface | **Telegram + CLI**, one shared formatter. |
| D5 | Recommendation output | **Never.** Flags and facts only; no buy/sell/hold. Decision support, not a decision. |
| D6 | What we persist | **Derived scored metrics as a cache**, latest-only, not raw statements or historical snapshots. |

---

## 13. Later ideas (out of scope, recorded)

- Sector-relative scorecard thresholds once there's a reference dataset to calibrate against.
- A fundamentals *filter* in the nightly scan (only alert in-range symbols that also pass a minimum scorecard) — needs cross-symbol caching discipline first.
- Auto-teaser (the declined D2 option) if on-demand proves too much friction.
- Feeding dossier verdicts into the Phase 2 paper-trading entry rule.

---

## 14. Milestones (Phase 4)

Milestone IDs use an `F`-prefix to avoid collision with Phase 1's `M0–M7` in `tasks.md`; tasks are `F<n>T<nn>`; branch names `p4-<slug>`; commit subjects `P4 F<n>: <what>`. No Claude attribution in git (`CLAUDE.md`).

| # | Deliverable | Definition of done |
|---|---|---|
| F1 | Ports + domain models | `FundamentalsProvider`/`NewsProvider`/`SummaryProvider` Protocols + `CompanyProfile`/`FundamentalsSnapshot`/`NewsItem`/`Scorecard`/`Dossier` dataclasses; import-linter contract updated; mypy-strict clean. |
| F2 | Scorecard engine | `scorecard.py` passes unit tests against 5 hand-checked fixtures (mega-cap, value, growth-unprofitable, high-debt, insufficient-data). |
| F3 | Provider adapters | FMP + Finnhub + yfinance-fallback adapters pass contract tests replaying recorded JSON frames (no network); graceful degradation on primary failure verified. |
| F4 | Caching & persistence | New repo methods + SQLite/DynamoDB stores pass the shared repository contract suite; freshness rule (§4.3) covered. |
| F5 | Dossier assembly + delivery | `build_dossier` + formatter wired; `/dossier` (Telegram) and `screener dossier` (CLI) return a correct report end-to-end against fakes; cache-hit path makes zero external calls. |
| F6 | Optional AI summary | `AnthropicSummaryProvider` behind the toggle; `--ai`/config on adds exactly one LLM call, off adds none; provider mocked in tests. |

---

## 15. Verification

Standard gate (README) after each milestone and before sign-off:

```
poetry run ruff check
poetry run mypy src
poetry run lint-imports
poetry run pytest
```

End-to-end checks ("drive the real flow"):
1. **Cold dossier, CLI:** `poetry run screener dossier AAPL` → full report renders; footer shows FMP + Finnhub sources and fresh timestamps.
2. **Cache hit:** rerun immediately → same report, logs show **zero** external fundamentals/news calls (SC-2).
3. **Scorecard audit:** run against the 5 fixtures; flags match hand-computed expectations (SC-3).
4. **Provider outage:** force FMP to error (bad key / injected failure) → dossier still returns via yfinance fallback with a footer note; nothing raises to the user (SC-4).
5. **Telegram:** `/dossier AAPL` from the authorized chat → chunked message; unauthorized chat ignored.
6. **AI toggle:** `--ai` adds one Haiku call and an "AI read" section; default run makes no LLM call (SC-5).