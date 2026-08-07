"""Real Telegram Bot API delivery (FR-5). Honours the ``Notifier`` contract: chunk at 4096
chars, retry 3× per chunk, and **never raise** — a failed alert must never crash the scheduler.

Plain-text delivery only: the digest contains ``$ · ~ | + −``, which would break Telegram's
Markdown/HTML parsers and return HTTP 400, so no ``parse_mode`` is set.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable

import httpx

from screener.domain.models import DeliveryStatus

log = logging.getLogger("screener.telegram")

# (url, json_payload, timeout_seconds) -> HTTP status code. Injected for testability.
PostFn = Callable[[str, dict[str, object], float], int]

TELEGRAM_LIMIT = 4096


def chunk_message(message: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split a message into <= ``limit``-char pieces, preferring line boundaries so digest
    rows are never cut mid-line. A single line longer than ``limit`` is hard-split."""
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current = ""
    for line in message.split("\n"):
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), limit):
                piece = line[i : i + limit]
                if len(piece) == limit:
                    chunks.append(piece)
                else:
                    current = piece  # remainder < limit; keep accumulating
            continue

        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def _httpx_post(url: str, payload: dict[str, object], timeout: float) -> int:
    return httpx.post(url, json=payload, timeout=timeout).status_code


class TelegramNotifier:
    """Delivers digests to a single Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        limit: int = TELEGRAM_LIMIT,
        post: PostFn | None = None,
        sleep: Callable[[float], None] = _time.sleep,
    ) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._limit = limit
        self._post = post or _httpx_post
        self._sleep = sleep

    def send(self, message: str) -> DeliveryStatus:
        try:
            for chunk in chunk_message(message, self._limit):
                if not self._send_chunk(chunk):
                    return DeliveryStatus.FAILED
            return DeliveryStatus.SENT
        except Exception as exc:  # noqa: BLE001 — a failed alert never crashes the scheduler (FR-5)
            # Log the type only, never the exception/traceback: httpx errors can carry the
            # request URL, which embeds the bot token in its path.
            log.warning("telegram delivery failed unexpectedly: %s", type(exc).__name__)
            return DeliveryStatus.FAILED

    def _send_chunk(self, text: str) -> bool:
        payload: dict[str, object] = {"chat_id": self._chat_id, "text": text}
        for attempt in range(1, self._max_retries + 1):
            try:
                status = self._post(self._url, payload, self._timeout)
                if 200 <= status < 300:
                    return True
                log.warning(
                    "telegram sendMessage HTTP %s (attempt %d/%d)",
                    status,
                    attempt,
                    self._max_retries,
                )
            except Exception as exc:  # noqa: BLE001 — retry, then give up; never propagate
                # Type only — an httpx error's repr/traceback can leak the token-bearing URL.
                log.warning(
                    "telegram sendMessage raised %s (attempt %d/%d)",
                    type(exc).__name__,
                    attempt,
                    self._max_retries,
                )
            if attempt < self._max_retries:
                self._sleep(self._backoff * attempt)
        return False
