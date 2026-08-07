# MA150 / ATR Proximity Screener

A scheduled service that watches a user-managed universe of US-listed stocks, computes SMA150 and
ATR14 from completed daily bars, and reports which symbols trade within 1.5×ATR of their 150-day
moving average. See [docs/PRD-stock-screener.md](docs/PRD-stock-screener.md) and
[docs/architecture.md](docs/architecture.md).

## Status

Phase 1, core (M0–M3): domain + ports, indicator math, SQLite data layer, and an end-to-end scan
pipeline runnable from the CLI. Telegram, scheduling, and cloud deploy are deferred.

## Development

```bash
poetry install
poetry run ruff check
poetry run mypy src
poetry run lint-imports
poetry run pytest
```

## CLI

```bash
poetry run screener backfill AAPL KO NVDA     # fetch + cache 2y of daily bars
poetry run screener scan --once --type CLOSE  # run one scan, persist scan_results
poetry run screener universe list             # show the universe
```
