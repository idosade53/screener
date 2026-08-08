# Tasks — MA150 / ATR Proximity Screener (Phase 1)

Derived from `docs/PRD-stock-screener.md` (§15 milestones) and `docs/architecture.md` (§15 build
order, M0–M7 — more granular than the PRD, matches the actual package layout, used here).

Tasks are numbered `M<milestone>T<task>`, e.g. `M2T05` = milestone 2, task 5.

**Status: M0–M5 done and green; M7 (Lambda + DynamoDB) in progress — Stages 1–2 landed
(DynamoDB adapter + 3 Lambda handlers + SSM secrets), 135 tests green; Stage 3 (Dockerfile +
.dockerignore + deploy docs) built `linux/amd64` and RIE-smoke-tested; Stage 4 Terraform (`infra/`)
authored. Remaining: `terraform validate`/`apply` on a machine with Terraform + AWS creds, push
image, set secrets, register webhook, observe 5 clean trading days.** M6 in-process scheduling (M6T02–T04) is the RPi
target and is out of scope for the Lambda deployment (EventBridge replaces it). (M5 = command/
dispatch logic; the live Telegram long-poll transport is `composition/bot_runner.py`.)

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

- [x] **M5T01** `bot/commands.py` — `/add`, `/remove`, `/list`, `/status`, `/scan`, `/help` (FR-1).
      Handlers take a narrow `bot/context.py::BotContext` (repo + settings + a `run_scan` callable),
      not the composition `Application` — the import-linter `layers` contract puts `bot` below
      `composition`, so a handler importing `Application` would be an upward-import build failure.
- [x] **M5T02** `bot/auth.py::is_authorized` — allowlist = the configured `telegram_chat_id`
      (single-operator Phase 1); `dispatch` returns `None` (silently ignored) for any other chat.
      **Security-critical:** this gate runs FIRST in `dispatch`, before parsing/routing any command,
      so `/scan`/`/add`/`/remove` are unreachable from an unauthorized chat.
- [x] **M5T03** `bot/dispatch.py::dispatch(update, ctx)` — update → command → response; strips
      `/` and any `@botname` suffix, unknown/free-text → help, never raises (handler errors →
      apology reply, mirroring the notifier contract). Seam the M7 long-poll loop calls.
- [x] **M5T04** Symbol validation relocated to `screener/symbols.py::normalise` (shared, returns
      structured `valid`/`invalid` instead of stderr prints so the bot can report invalids in-reply);
      `composition/cli.py::_normalise` now delegates to it (CLI behaviour unchanged).
- [x] **M5T05** Universe cap soft-guard on `/add` — accepts up to `settings.universe_cap`
      (was defined but **enforced nowhere** before M5), reports the overflow as "cap reached".
- [x] **M5T06** Idempotent command handling — inherited from the repo (`add_symbols` re-activates,
      `remove_symbol` soft-deletes); handlers report the effective outcome (added / already-present /
      not-in-universe). Unit-tested in `tests/unit/test_bot.py`.
- Composition seam: `composition/wiring.py::build_bot_context(app)` builds the `BotContext`
  (`run_scan = lambda: app.pipeline().run(ScanType.MANUAL)`) for the future M7 transport.

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
      Lambda). The `.env` holds the bot token + chat_id — deploy must `chmod 600 .env` so it is
      not group/world-readable.
- [ ] **M7T02** Nightly DB backup (plain file copy per PRD FR-6)
- [ ] **M7T03** Telegram long-poll loop wired into `rpi_main.py` (replaces webhook for the Pi
      target)
- [ ] **M7T04** Docker Compose deployment (PRD M7 DoD, target-agnostic per architecture §14 R3)
- [ ] **M7T05** 5 consecutive clean trading days observed post-deploy
- [ ] **M7T06** *(Lambda path — now the chosen Phase-1 target):* delivered in stages.
  - [x] **Stage 1** `adapters/repository/dynamodb_repository.py` — full `ScreenerRepository` over
        the §8.3 single table (`screener`); passes the shared `RepositoryContract` under moto
        (`tests/contract/test_dynamodb_repository.py`, 16 tests, no network). `boto3` runtime dep +
        `moto` dev dep added; `composition/wiring.py` builds the Dynamo table when
        `repository_backend == "dynamodb"` (`dynamodb_table`/`aws_region` in `config.py`).
  - [x] **Stage 2** Lambda composition roots (unit/integration-tested with fakes + moto, no
        network): `composition/lambda_scan.py` (EventBridge `scan_type` → pipeline),
        `lambda_webhook.py` (Function-URL Telegram receiver reusing `bot_runner._process`, with a
        `X-Telegram-Bot-Api-Secret-Token` gate on top of the M5 chat allowlist), `lambda_export.py`
        (full DynamoDB scan → FR-6 SQLite rebuild → atomic S3 swap, §9.4), and
        `composition/secrets.py` (SSM Parameter Store overlay when `SCREENER_SSM_PREFIX` is set,
        else plain `.env`). New `config.py` fields: `telegram_webhook_secret`, `export_bucket`,
        `export_key`.
  - [x] **Stage 3** container image: multi-stage `Dockerfile` (python:3.13-slim builder exports
        locked deps + builds the wheel → AWS Lambda Python 3.13 base, deps into `${LAMBDA_TASK_ROOT}`,
        dev group excluded, `--no-compile`); one image / three handlers selected by
        `image_config.command`; `.dockerignore` to slim the context; build/RIE-smoke-test steps in
        `docs/deploy.md`. **Built `linux/amd64` and RIE-smoke-tested** — scan handler returns a valid
        response (+ proper error payload for a bad type), webhook processes an authorized `/list` and
        silently 200s an unauthorized chat. Image ≈1.06 GB (Lambda base + pandas/numpy dominate;
        within the 10 GB limit — trims noted in `docs/deploy.md`).
  - [~] **Stage 4** Terraform infra in `infra/` (authored; **not yet `terraform validate`d/applied**
        — no Terraform binary in the authoring env): `dynamodb.tf` (5/5 provisioned + PITR),
        `ecr.tf` (2-image lifecycle), `lambda.tf` (3 image functions off one image + webhook Function
        URL), `scheduler.tf` (EventBridge Scheduler ET cron PRE/OPEN/CLOSE + daily export),
        `s3.tf` (versioned export bucket), `ssm.tf` (secret placeholders, values set out-of-band),
        `iam.tf` (per-function least-privilege + scheduler role), `monitoring.tf` ($1 billing alarm),
        plus `outputs.tf`, `variables.tf`, `README.md` runbook. Scheduling is EventBridge here — the
        RPi APScheduler wiring (M6T02–T04) is **not** needed for this target.
  - [ ] **Deploy/verify** (needs AWS creds + Docker host): `terraform fmt/validate/apply`, push image,
        set SSM secrets, register Telegram webhook, then observe 5 consecutive clean trading days
        (M7T05).

---

## Deferred by design (not a gap)

- **DynamoDB repository adapter** — *no longer deferred.* The Lambda deployment path (§8) is now
  being pursued, so `adapters/repository/dynamodb_repository.py` has landed (M7T06 Stage 1) and
  `composition/wiring.py` selects it on `repository_backend == "dynamodb"`. SQLite remains the
  RPi/local default and the §8.5 "path 2" migration artefact; both back the same contract suite.

## Not yet covered by any milestone above

- [ ] **MXT01** SC-3 spot audit: 10-symbol manual TradingView/Excel cross-check (PRD SC-3) —
      distinct from the automated fixture tests already in `test_atr.py`/`test_sma.py`
- [ ] **MXT02** Second criterion extensibility point (§10/§11.1) — no second criterion exists yet;
      the `Criterion` protocol and registry are in place but only exercised with one implementation
