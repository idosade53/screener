"""The Telegram command loop (M7T03), wired at the composition level so it can own both the
transport adapter and the ``bot`` dispatcher — the two live in different layers and only the
composition root may bridge them (architecture §3, rule 3). This is the loop the
``TODO(M4/M5)`` in ``rpi_main.py`` referred to.

``poll_loop`` is the testable core (drive it with a fake transport + bounded ``max_batches``);
``run_bot`` builds the real transport from settings and loops forever."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from screener.adapters.notify.telegram_transport import TelegramTransport
from screener.bot.context import BotContext
from screener.bot.dispatch import dispatch
from screener.bot.models import BotUpdate
from screener.composition.wiring import Application, build_bot_context
from screener.domain.errors import ConfigError

log = logging.getLogger("screener.bot")


class Transport(Protocol):
    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict[str, Any]]: ...
    def send_message(self, chat_id: str, text: str) -> None: ...


def _process(raw: dict[str, Any], ctx: BotContext) -> tuple[str, str] | None:
    """Turn one raw Telegram update into a (chat_id, reply) to send, or None to stay silent."""
    msg = raw.get("message") or raw.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = str(chat.get("id"))
    text = str(msg.get("text") or "")
    reply = dispatch(
        BotUpdate(update_id=int(raw["update_id"]), chat_id=chat_id, text=text), ctx
    )
    return None if reply is None else (chat_id, reply)


def poll_loop(
    ctx: BotContext,
    transport: Transport,
    *,
    poll_timeout: int = 30,
    max_batches: int | None = None,
) -> None:
    offset: int | None = None
    batches = 0
    while max_batches is None or batches < max_batches:
        updates = transport.get_updates(offset, poll_timeout)
        batches += 1
        for raw in updates:
            offset = int(raw["update_id"]) + 1  # ack: never re-deliver this update
            processed = _process(raw, ctx)
            if processed is not None:
                chat_id, reply = processed
                transport.send_message(chat_id, reply)
                log.info("replied to chat %s (%d chars)", chat_id, len(reply))


def run_bot(app: Application, *, poll_timeout: int = 30) -> None:
    if not app.settings.telegram_bot_token or not app.settings.telegram_chat_id:
        raise ConfigError(
            "telegram_bot_token and telegram_chat_id must be set to run the bot"
        )
    ctx = build_bot_context(app)
    transport = TelegramTransport(app.settings.telegram_bot_token)
    log.info("telegram command loop started (poll_timeout=%ss)", poll_timeout)
    poll_loop(ctx, transport, poll_timeout=poll_timeout)
