"""Update → command → response (M5T03). The single seam the M7 long-poll loop will call:
hand it a :class:`BotUpdate` and a :class:`BotContext`, get back a reply string (or ``None`` to
send nothing). Never raises — an unauthorized chat is ignored silently and a failing handler
yields an apology, mirroring the notifier's must-not-raise contract."""

from __future__ import annotations

import logging

from screener.bot.auth import is_authorized
from screener.bot.commands import COMMANDS, cmd_help
from screener.bot.context import BotContext
from screener.bot.models import BotUpdate

log = logging.getLogger("screener.bot")


def dispatch(update: BotUpdate, ctx: BotContext) -> str | None:
    if not is_authorized(update.chat_id, ctx.settings):
        log.info("ignoring update from unauthorized chat_id=%s", update.chat_id)
        return None

    text = update.text.strip()
    if not text.startswith("/"):
        return cmd_help([], ctx)

    parts = text.split()
    token = parts[0][1:].split("@", 1)[0].lower()  # strip leading '/' and any '@botname' suffix
    args = parts[1:]

    handler = COMMANDS.get(token)
    if handler is None:
        return f"Unknown command: /{token}\n\n{cmd_help([], ctx)}"

    try:
        return handler(args, ctx)
    except Exception:  # a handler bug must never take down the poll loop
        log.exception("command /%s failed", token)
        return "⚠️ Sorry, that command failed. Please try again."
