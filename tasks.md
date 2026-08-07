# Tasks — MA150 / ATR Proximity Screener (Phase 1)

Derived from `docs/PRD-stock-screener.md` (§15 milestones) and `docs/architecture.md` (§15 build
order, M0–M7 — more granular than the PRD, matches the actual package layout, used here).

**Status: M0–M3 done and green (76 tests). M4 logic done, Telegram transport pending. M5–M7 not
started.**

---

## M0 — Domain types + ports + contract test skeletons

- [x] `domain/models.py` — `Symbol`, `Bar`, `ScanType`, `ScanContext`, `SymbolStatus`,
      `CriterionResult`, `ScanResult`, `ScanSummary`, `Diff`
- [x] `domain/errors.py` — typed exceptions
- [x] Five ports defined (`ports/market_data.py`, `repository.py`, `notifier.py`, `calendar.py`,
      `clock.py`)
- [x] `import-linter` rules enforced and passing (`poetry run lint-imports`)

## M1 — Indicator core

- [x] `indicators/sma.py` — `sma(series, period)`
- [x] `indicators/atr.py` — `atr(ohlc, 14)`, Wilder RMA, seeded with simple mean of first 14 TR
      values (PRD §4.2)
- [x] `indicators/quantize.py` — 4-dp `Decimal` quantisation at the numeric boundary
- [x] `indicators/registry.py` — name → callable, for Phase 2 reuse
- [x] Unit tests vs. hand-checked fixtures (`tests/unit/test_sma.py`, `test_atr.py`)

## M2 — Data layer

- [x] `adapters/market_data/yfinance_provider.py` — batched fetch, unadjusted OHLC, retry with
      backoff, `validate_symbol`
- [x] `adapters/repository/sqlite_repository.py` — full `ScreenerRepository` implementation
- [x] `adapters/calendar/xnys_calendar.py` — `TradingCalendar` over XNYS
- [x] `adapters/clock/system_clock.py` — `Clock` (tz-aware UTC)
- [x] Contract test suite shared across adapters (`tests/contract/repository_contract.py`,
      `test_sqlite_repository.py`, `test_yfinance_provider.py`, `test_xnys_calendar.py`)
- [x] Split detection — `screener/corporate_actions.py::detect_split` (ratio-vs-known-fractions
      check, §7.3), unit-tested (`test_corporate_actions.py`)
- [ ] Verify against a live 150-symbol backfill for delta-refresh + split-detection at real scale
      (PRD M2 DoD: "150 symbols fetched + cached"; currently exercised in tests/CLI at small scale
      only)

## M3 — Pipeline end-to-end via CLI

- [x] `screener/pipeline.py` — 10-stage scan pipeline (resolve context → claim → load universe →
      refresh bars → indicators → prices → evaluate → persist → diff → notify)
- [x] `screener/context.py` — `ScanContext` resolution (`ScanType` → bar window + price mode)
- [x] `screener/criterion.py` — `Criterion` protocol + `MA150ProximityCriterion`
- [x] Staleness guard (§7.2) — abort + `⚠️ SYSTEM` on universe-wide stale bars, no persistence
- [x] Idempotent claim (`try_claim_scan`) — duplicate invocation exits silently
- [x] Indicator cache validated on every read (`asof` mismatch → recompute, §7.4)
- [x] `composition/wiring.py` — composition root, `sqlite` backend wired; `dynamodb` raises
      `ConfigError` (deferred by design, see below)
- [x] `composition/cli.py` — `screener {backfill, scan --once, universe {list,add,remove}}`
- [x] Integration tests against fake adapters (`tests/integration/test_pipeline.py`):
      scripted sequence, per-symbol failure isolation, staleness abort, insufficient-history,
      idempotent claim
- [x] `ruff` / `mypy --strict` / `import-linter` / `pytest` all clean

## M4 — Formatters + diff + Telegram out

- [x] `screener/diff.py::compute_diff` — change detection: send on `MANUAL`, first-of-day
      baseline (Q1), or set change (FR-4)
- [x] `screener/formatters.py::format_scan_message` — digest formatting matching PRD FR-5 mockup
      (sorted by `abs(distance)`, `~` stale-price prefix, entered/exited, failure footer)
- [x] Scripted 3-scan sequence test proving exact message set
      (`test_pipeline.py::test_scripted_sequence_baseline_suppression_and_manual`)
- [x] `adapters/notify/console_notifier.py` — `ConsoleNotifier` (stdout) / `CollectingNotifier`
      (in-memory, for tests) — stand-ins used until the real adapter lands
- [ ] `adapters/notify/telegram_notifier.py` — real Telegram Bot API delivery: send message,
      4096-char chunking, 3× retry, never raises (FR-5, `ports/notifier.py` contract)
- [ ] 4096-char message chunking logic + test
- [ ] Missed-scan gap check — `⚠️ SYSTEM missed <TYPE>` when `scans_on(trading_day)` is missing an
      expected scan type (architecture §8.5 / failure taxonomy row 10)
- [ ] "All quiet" heartbeat — standalone ping if 3 consecutive trading days produce no message
      (FR-7)

## M5 — Bot commands in

- [ ] `bot/commands.py` — `/add`, `/remove`, `/list`, `/status`, `/scan`, `/help` (FR-1)
- [ ] `bot/auth.py` — `TELEGRAM_CHAT_ID` allowlist; other chats ignored silently
- [ ] `bot/dispatch.py` — update → command → response
- [ ] Symbol validation regex `^[A-Z][A-Z0-9.\-]{0,9}$` applied at the bot boundary (architecture
      §8.6) — note: already applied in `composition/cli.py::_normalise`, needs reuse/relocation
      for the bot path
- [ ] Universe cap (300) soft-guard on `/add` (FR-1)
- [ ] Idempotent command handling for Telegram redelivery (`/add` twice = one symbol, `/remove`
      of absent symbol = no-op) (architecture §8.4)

## M6 — Calendar + scheduling

- [x] Calendar correctness (the *calendar* half): frozen-clock contract tests for trading day /
      weekend / New Year's holiday / previous-trading-day-skips-weekend / regular vs. half-day
      session close (`tests/contract/test_xnys_calendar.py`)
- [ ] Scheduling wiring (the *scheduling* half): APScheduler jobs for `PRE`/`OPEN`/`CLOSE` in
      `composition/rpi_main.py::run()` — currently a documented no-op (`max_instances=1`,
      `coalesce=True` per architecture §8.4)
- [ ] Frozen-clock test matrix at the *scheduling* level: a full trading day, a weekend, a
      holiday, and a half-day drive the right scan types at the right times (PRD M6 DoD)
- [ ] `SCAN_TIMES_ET` config wired to the scheduler (currently only a `Settings` field, §8.2)

## M7 — Deploy

- [ ] Secrets externalised (`.env` for RPi/local; SSM Parameter Store deferred with Lambda)
- [ ] Nightly DB backup (plain file copy per PRD FR-6)
- [ ] Telegram long-poll loop wired into `rpi_main.py` (replaces webhook for the Pi target)
- [ ] Docker Compose deployment (PRD M7 DoD, target-agnostic per architecture §14 R3)
- [ ] 5 consecutive clean trading days observed post-deploy
- [ ] *(Lambda path, if pursued instead of/before RPi):* `composition/lambda_scan.py`,
      `lambda_webhook.py`, `lambda_export.py`, container image + ECR lifecycle policy,
      EventBridge Scheduler rules, DynamoDB table + `dynamodb_repository.py`

---

## Deferred by design (not a gap)

- **DynamoDB repository adapter** — `composition/wiring.py` raises `ConfigError` for any backend
  other than `sqlite`. This is an intentional current choice (SQLite-first), not an oversight: the
  SQLite implementation already satisfies the same `ScreenerRepository` contract tests, and
  architecture §8.5 "path 2" treats the SQLite file as the eventual RPi5 migration artefact
  regardless. Add `dynamodb_repository.py` only if the Lambda deployment path (§8) is actually
  pursued.

## Not yet covered by any milestone above

- [ ] SC-3 spot audit: 10-symbol manual TradingView/Excel cross-check (PRD SC-3) — distinct from
      the automated fixture tests already in `test_atr.py`/`test_sma.py`
- [ ] Second criterion extensibility point (§10/§11.1) — no second criterion exists yet; the
      `Criterion` protocol and registry are in place but only exercised with one implementation
