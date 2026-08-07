"""CLI composition root (M3): backfill, one-off scan, universe management, validate. Runs
locally and on the RPi5 unchanged; Lambda handlers are a later milestone."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Sequence
from datetime import timedelta

from screener.composition.wiring import Application, build_application
from screener.domain.models import ScanType

_SYMBOL_RE = r"^[A-Z][A-Z0-9.\-]{0,9}$"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="screener", description="MA150/ATR screener")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="run a single scan and persist results")
    p_scan.add_argument("--once", action="store_true", help="run once and exit")
    p_scan.add_argument(
        "--type",
        dest="scan_type",
        choices=[t.value for t in ScanType],
        default=ScanType.MANUAL.value,
    )

    p_backfill = sub.add_parser("backfill", help="fetch and cache history for symbols")
    p_backfill.add_argument("symbols", nargs="+")

    p_uni = sub.add_parser("universe", help="manage the watched universe")
    uni_sub = p_uni.add_subparsers(dest="universe_command", required=True)
    uni_sub.add_parser("list")
    p_add = uni_sub.add_parser("add")
    p_add.add_argument("symbols", nargs="+")
    p_remove = uni_sub.add_parser("remove")
    p_remove.add_argument("symbols", nargs="+")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    app = build_application()

    if args.command == "scan":
        summary = app.pipeline().run(ScanType(args.scan_type))
        print(
            f"\n[{summary.status.value}] {summary.scan_id}: "
            f"{len(summary.in_range)} in range, {summary.symbols_scanned} scanned"
        )
        return 0

    if args.command == "backfill":
        return _backfill(app, _normalise(args.symbols))

    if args.command == "universe":
        return _universe(app, args)

    return 1


def _normalise(symbols: Sequence[str]) -> list[str]:
    out: list[str] = []
    for s in symbols:
        u = s.strip().upper()
        if not re.match(_SYMBOL_RE, u):
            print(f"skip invalid symbol: {s!r}", file=sys.stderr)
            continue
        if u not in out:
            out.append(u)
    return out


def _backfill(app: Application, symbols: list[str]) -> int:
    if not symbols:
        print("no valid symbols", file=sys.stderr)
        return 1

    # Ensure they are in the universe, then fetch a full history window and cache it.
    app.repo.add_symbols(symbols)
    end = app.clock.now().date()
    start = end - timedelta(days=365 * app.settings.backfill_years + 10)
    result = app.provider.fetch_daily_bars(symbols, start, end)
    for sym, bars in result.bars.items():
        app.repo.upsert_bars(sym, bars)
        print(f"{sym}: cached {len(bars)} bars (through {bars[-1].date if bars else 'n/a'})")
    for sym, reason in result.failures.items():
        print(f"{sym}: FAILED — {reason}", file=sys.stderr)
    return 0


def _universe(app: Application, args: argparse.Namespace) -> int:
    cmd = args.universe_command
    if cmd == "list":
        members = app.repo.get_universe()
        for m in members:
            print(m.symbol)
        print(f"\n{len(members)} symbols")
        return 0
    if cmd == "add":
        symbols = _normalise(args.symbols)
        app.repo.add_symbols(symbols)
        print(f"added: {', '.join(symbols) or '(none)'}")
        return 0
    if cmd == "remove":
        for s in _normalise(args.symbols):
            app.repo.remove_symbol(s)
        print("removed")
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
