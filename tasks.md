# Tasks — MA150 / ATR Proximity Screener (Phase 1)

Derived from `docs/PRD-stock-screener.md` (§15 milestones) and `docs/architecture.md` (§15 build
order, M0–M7 — more granular than the PRD, matches the actual package layout, used here).

Tasks are numbered `M<milestone>T<task>`, e.g. `M2T05` = milestone 2, task 5.

**Status: M0–M4 done and green (88 tests). M5–M7 not started.**

---

## M0 — Domain types + ports + contract test skeletons

- [x] **M0T01** `domain/models.py` — `Symbol`, `Bar`, `ScanType`, `ScanContext`, `SymbolStatus`,
      `CriterionResult`, `ScanResult`, `ScanSummary`, `Diff`
- [x] **M0T02** `domain/errors.py` — typed exceptions
- [x] **M0T03** Five ports defined (`ports/market_data.py`, `repository.py`, `notifier.py`,
      `calendar.py`, `clock.py`)
- [x] **M0T04** `import-linter` rules enforced and passing (`poetry run lint-imports`)

## M1 — Indicator core

- [x] **M1T01** `indicators/sma.py` — `sma(series, period)`
- [x] **M1T02** `indicators/atr.py` — `atr(ohlc, 14)`, Wilder RMA, seeded with simple mean of first
      14 TR values (PRD §4.2)
- [x] **M1T03** `indicators/quantize.py` — 4-dp `Decimal` quantisation at the numeric boundary
- [x] **M1T04** `indicators/registry.py` — name → callable, for Phase 2 reuse
- [x] **M1T05** Unit tests vs. hand-checked fixtures (`tests/unit/test_sma.py`, `test_atr.py`)

## M2 — Data layer

- [x] **M2T01** `adapters/market_data/yfinance_provider.py` — batched fetch, unadjusted OHLC,
      retry with backoff, `validate_symbol`
- [x] **M2T02** `adapters/repository/sqlite_repository.py` — full `ScreenerRepository`
      implementation
- [x] **M2T03** `adapters/calendar/xnys_calendar.py` — `TradingCalendar` over XNYS
- [x] **M2T04** `adapters/clock/system_clock.py` — `Clock` (tz-aware UTC)
- [x] **M2T05** Contract test suite shared across adapters (`tests/contract/repository_contract.py`,
      `test_sqlite_repository.py`, `test_yfinance_provider.py`, `test_xnys_calendar.py`)
- [x] **M2T06** Split detection — `screener/corporate_actions.py::detect_split`
      (ratio-vs-known-fractions check, §7.3), unit-tested (`test_corporate_actions.py`)
- [ ] **M2T07** Verify against a live 150-symbol backfill for delta-refresh + split-detection at
      real scale (PRD M2 DoD: "150 symbols fetched + cached"; currently exercised in tests/CLI at
      small scale only)

## M3 — Pipeline end-to-end via CLI

- [x] **M3T01** `screener/pipeline.py` — 10-stage scan pipeline (resolve context → claim → load
      universe → refresh bars → indicators → prices → evaluate → persist → diff → notify)
- [x] **M3T02** `screener/context.py` — `ScanContext` resolution (`ScanType` → bar window + price
      mode)
- [x] **M3T03** `screener/criterion.py` — `Criterion` protocol + `MA150ProximityCriterion`
- [x] **M3T04** Staleness guard (§7.2) — abort + `⚠️ SYSTEM` on universe-wide stale bars, no
      persistence
- [x] **M3T05** Idempotent claim (`try_claim_scan`) — duplicate invocation exits silently
- [x] **M3T06** Indicator cache validated on every read (`asof` mismatch → recompute, §7.4)
- [x] **M3T07** `composition/wiring.py` — composition root, `sqlite` backend wired; `dynamodb`
      raises `ConfigError` (deferred by design, see below)
- [x] **M3T08** `composition/cli.py` — `screener {backfill, scan --once, universe {list,add,remove}}`
- [x] **M3T09** Integration tests against fake adapters (`tests/integration/test_pipeline.py`):
      scripted sequence, per-symbol failure isolation, staleness abort, insufficient-history,
      idempotent claim
- [x] **M3T10** `ruff` / `mypy --strict` / `import-linter` / `pytest` all clean

## M4 — Formatters + diff + Telegram out

- [x] **M4T01** `screener/diff.py::compute_diff` — change detection: send on `MANUAL`,
      first-of-day baseline (Q1), or set change (FR-4)
- [x] **M4T02** `screener/formatters.py::format_scan_message` — digest formatting matching PRD
      FR-5 mockup (sorted by `abs(distance)`, `~` stale-price prefix, entered/exited, failure
      footer)
- [x] **M4T03** Scripted 3-scan sequence test proving exact message set
      (`test_pipeline.py::test_scripted_sequence_baseline_suppression_and_manual`)
- [x] **M4T04** `adapters/notify/console_notifier.py` — `ConsoleNotifier` (stdout) /
      `CollectingNotifier` (in-memory, for tests) — stand-ins used until the real adapter lands
- [x] **M4T05** `adapters/notify/telegram_notifier.py` — real Telegram Bot API delivery: plain-text
      `sendMessage`, 4096-char chunking, 3× retry, never raises (FR-5, `ports/notifier.py` contract).
      Injectable `post`/`sleep` for network-free unit tests. Wired in `composition/wiring.py`:
      `TelegramNotifier` when `telegram_bot_token`+`telegram_chat_id` set, else `ConsoleNotifier`.
- [x] **M4T06** `telegram_notifier.py::chunk_message` — line-boundary chunking at 4096, hard-split
      for overlong single lines; unit-tested (`tests/unit/test_telegram_notifier.py`)
- [x] **M4T07** Missed-scan gap check — `ScanPipeline._check_missed_scans`, fires
      `⚠️ SYSTEM missed <TYPE>` from the first scan to run after a gap; deduped so a missing PRE is
      flagged once, not again by CLOSE (architecture §8.5 / failure taxonomy row 10). Integration-tested.
- [x] **M4T08** Daily heartbeat — `💓 Daily heartbeat` footer appended to every CLOSE message
      (`screener/formatters.py`), the CLOSE message being the daily proof-of-life (FR-7).
      *Design decision:* the standalone "3 quiet days" ping is unreachable given the Q1 first-of-day
      baseline sends a message every trading day, so the CLOSE-footer form was chosen instead;
      a set-change-streak ping can be revisited if that baseline behaviour ever changes.

## M5 — Bot commands in

- [ ] **M5T01** `bot/commands.py` — `/add`, `/remove`, `/list`, `/status`, `/scan`, `/help` (FR-1)
- [ ] **M5T02** `bot/auth.py` — `TELEGRAM_CHAT_ID` allowlist; other chats ignored silently
- [ ] **M5T03** `bot/dispatch.py` — update → command → response
- [ ] **M5T04** Symbol validation regex `^[A-Z][A-Z0-9.\-]{0,9}$` applied at the bot boundary
      (architecture §8.6) — note: already applied in `composition/cli.py::_normalise`, needs
      reuse/relocation for the bot path
- [ ] **M5T05** Universe cap (300) soft-guard on `/add` (FR-1)
- [ ] **M5T06** Idempotent command handling for Telegram redelivery (`/add` twice = one symbol,
      `/remove` of absent symbol = no-op) (architecture §8.4)

## M6 — Calendar + scheduling

- [x] **M6T01** Calendar correctness (the *calendar* half): frozen-clock contract tests for
      trading day / weekend / New Year's holiday / previous-trading-day-skips-weekend / regular
      vs. half-day session close (`tests/contract/test_xnys_calendar.py`)
- [ ] **M6T02** Scheduling wiring (the *scheduling* half): APScheduler jobs for `PRE`/`OPEN`/
      `CLOSE` in `composition/rpi_main.py::run()` — currently a documented no-op
      (`max_instances=1`, `coalesce=True` per architecture §8.4)
- [ ] **M6T03** Frozen-clock test matrix at the *scheduling* level: a full trading day, a weekend,
      a holiday, and a half-day drive the right scan types at the right times (PRD M6 DoD)
- [ ] **M6T04** `SCAN_TIMES_ET` config wired to the scheduler (currently only a `Settings` field,
      §8.2)

## M7 — Deploy

- [ ] **M7T01** Secrets externalised (`.env` for RPi/local; SSM Parameter Store deferred with
      Lambda)
- [ ] **M7T02** Nightly DB backup (plain file copy per PRD FR-6)
- [ ] **M7T03** Telegram long-poll loop wired into `rpi_main.py` (replaces webhook for the Pi
      target)
- [ ] **M7T04** Docker Compose deployment (PRD M7 DoD, target-agnostic per architecture §14 R3)
- [ ] **M7T05** 5 consecutive clean trading days observed post-deploy
- [ ] **M7T06** *(Lambda path, if pursued instead of/before RPi):* `composition/lambda_scan.py`,
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

- [ ] **MXT01** SC-3 spot audit: 10-symbol manual TradingView/Excel cross-check (PRD SC-3) —
      distinct from the automated fixture tests already in `test_atr.py`/`test_sma.py`
- [ ] **MXT02** Second criterion extensibility point (§10/§11.1) — no second criterion exists yet;
      the `Criterion` protocol and registry are in place but only exercised with one implementation
