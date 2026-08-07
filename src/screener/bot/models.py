"""Inbound command value object (M5). Transport-agnostic on purpose: the M7 long-poll
client parses raw Telegram ``getUpdates`` JSON into these, and tests construct them directly."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotUpdate:
    update_id: int
    chat_id: str
    text: str
