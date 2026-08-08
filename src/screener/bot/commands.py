"""Command handlers (M5T01, M5T05, M5T06). Each handler maps ``(args, ctx)`` to a reply
string; the caller decides how to deliver it. Handlers touch only ``ctx`` (ports + settings +
the scan callable), never adapters or the composition root.

Idempotency (M5T06) is inherited from the repository: ``add_symbols`` re-activates rather than
duplicating and ``remove_symbol`` is a soft delete, so re-issuing ``/add``/``/remove`` is safe.
The handlers just report the *effective* outcome (added / already-present / not-in-universe)."""

from __future__ import annotations

from collections.abc import Callable

from screener.bot.context import BotContext
from screener.domain.errors import UnknownSymbolError
from screener.fundamentals.formatters import format_dossier
from screener.screener.symbols import normalise

Handler = Callable[[list[str], BotContext], str]

HELP_TEXT = (
    "Commands:\n"
    "/list — show the watched universe\n"
    "/add SYM [SYM …] — add symbols\n"
    "/remove SYM [SYM …] — remove symbols\n"
    "/status — last scan summary\n"
    "/scan — run a manual scan now\n"
    "/dossier SYM (alias /dd) — fundamentals + news dossier\n"
    "/help — this message"
)


def cmd_help(args: list[str], ctx: BotContext) -> str:
    return HELP_TEXT


def cmd_list(args: list[str], ctx: BotContext) -> str:
    members = ctx.repo.get_universe()
    if not members:
        return "Universe is empty."
    body = "\n".join(m.symbol for m in members)
    return f"{body}\n\n{len(members)} symbols"


def cmd_add(args: list[str], ctx: BotContext) -> str:
    if not args:
        return "Usage: /add SYM [SYM …]"
    result = normalise(args)
    active = {m.symbol for m in ctx.repo.get_universe()}
    already = [s for s in result.valid if s in active]
    candidates = [s for s in result.valid if s not in active]

    # Soft-guard the universe cap (M5T05): accept up to the remaining room, report the overflow.
    cap = ctx.settings.universe_cap
    room = max(cap - len(active), 0)
    to_add = candidates[:room]
    capped = candidates[room:]
    if to_add:
        ctx.repo.add_symbols(to_add)

    lines: list[str] = []
    if to_add:
        lines.append(f"Added: {', '.join(to_add)}")
    if already:
        lines.append(f"Already present: {', '.join(already)}")
    if capped:
        lines.append(f"Not added — universe cap {cap} reached: {', '.join(capped)}")
    if result.invalid:
        lines.append(f"Invalid, skipped: {', '.join(result.invalid)}")
    return "\n".join(lines) if lines else "Nothing to add."


def cmd_remove(args: list[str], ctx: BotContext) -> str:
    if not args:
        return "Usage: /remove SYM [SYM …]"
    result = normalise(args)
    active = {m.symbol for m in ctx.repo.get_universe()}
    removed = [s for s in result.valid if s in active]
    absent = [s for s in result.valid if s not in active]
    for s in removed:
        ctx.repo.remove_symbol(s)

    lines: list[str] = []
    if removed:
        lines.append(f"Removed: {', '.join(removed)}")
    if absent:
        lines.append(f"Not in universe: {', '.join(absent)}")
    if result.invalid:
        lines.append(f"Invalid, skipped: {', '.join(result.invalid)}")
    return "\n".join(lines) if lines else "Nothing to remove."


def cmd_status(args: list[str], ctx: BotContext) -> str:
    summary = ctx.repo.latest_scan()
    if summary is None:
        return "No scans have run yet."
    return (
        f"Last scan: {summary.scan_type.value} [{summary.status.value}]\n"
        f"Ran at: {summary.ran_at.isoformat()}\n"
        f"Scanned: {summary.symbols_scanned}\n"
        f"In range: {len(summary.in_range)}"
    )


def cmd_scan(args: list[str], ctx: BotContext) -> str:
    summary = ctx.run_scan()
    return (
        f"Scan complete: {summary.scan_type.value} [{summary.status.value}]\n"
        f"{len(summary.in_range)} in range, {summary.symbols_scanned} scanned\n"
        f"(id {summary.scan_id})"
    )


def cmd_dossier(args: list[str], ctx: BotContext) -> str:
    if not args:
        return "Usage: /dossier SYM"
    result = normalise(args[:1])
    if not result.valid:
        return f"Invalid symbol: {', '.join(result.invalid) or args[0]}"
    symbol = result.valid[0]
    try:
        dossier = ctx.build_dossier(symbol)
    except UnknownSymbolError:
        return f"Unknown symbol: {symbol}. Check the ticker and try again."
    return format_dossier(dossier)


COMMANDS: dict[str, Handler] = {
    "help": cmd_help,
    "list": cmd_list,
    "add": cmd_add,
    "remove": cmd_remove,
    "status": cmd_status,
    "scan": cmd_scan,
    "dossier": cmd_dossier,
    "dd": cmd_dossier,  # alias (PRD §5)
}
