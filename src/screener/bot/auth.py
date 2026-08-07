"""Chat-id allowlist (M5T02). Phase-1 is single-operator, so the allowlist is exactly the
configured ``telegram_chat_id`` (also the outbound delivery target). Unauthorized chats are
dropped silently by the dispatcher — no reply, per architecture §8.6."""

from __future__ import annotations

from screener.config import Settings


def is_authorized(chat_id: str, settings: Settings) -> bool:
    allowed = settings.telegram_chat_id
    return allowed is not None and chat_id == allowed
