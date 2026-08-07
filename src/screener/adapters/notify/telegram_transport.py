"""Inbound Telegram transport (the M7T03 long-poll receiver). A *dumb* adapter: it only knows
how to ``getUpdates`` and ``sendMessage`` over HTTP — it holds no command logic and imports
nothing from ``bot`` (that would be an upward-layer import). The composition-level poll loop
(``composition/bot_runner.py``) owns dispatch and injects this as the wire.

Mirrors ``telegram_notifier.py``: injectable transport for tests, token kept out of every log
line (an httpx error's repr can embed the token-bearing URL), and never raises into the loop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from screener.adapters.notify.telegram_notifier import TELEGRAM_LIMIT, chunk_message

log = logging.getLogger("screener.telegram")

# (url, params, timeout_seconds) -> parsed JSON body. Injected for testability.
GetFn = Callable[[str, dict[str, str | int], float], Any]
# (url, json_payload, timeout_seconds) -> HTTP status code.
PostFn = Callable[[str, dict[str, object], float], int]


def _httpx_get(url: str, params: dict[str, str | int], timeout: float) -> Any:
    return httpx.get(url, params=params, timeout=timeout).json()


def _httpx_post(url: str, payload: dict[str, object], timeout: float) -> int:
    return httpx.post(url, json=payload, timeout=timeout).status_code


class TelegramTransport:
    def __init__(
        self,
        bot_token: str,
        *,
        send_timeout: float = 10.0,
        poll_timeout_buffer: float = 10.0,
        limit: int = TELEGRAM_LIMIT,
        get: GetFn | None = None,
        post: PostFn | None = None,
    ) -> None:
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._send_timeout = send_timeout
        self._poll_timeout_buffer = poll_timeout_buffer
        self._limit = limit
        self._get = get or _httpx_get
        self._post = post or _httpx_post

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict[str, Any]]:
        """Long-poll for updates. Returns the ``result`` array (possibly empty). Never raises —
        a transient network error yields an empty batch and the loop retries."""
        params: dict[str, str | int] = {"timeout": poll_timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            data = self._get(
                f"{self._base}/getUpdates", params, poll_timeout + self._poll_timeout_buffer
            )
        except Exception as exc:  # noqa: BLE001 — never crash the loop; retry next iteration
            log.warning("telegram getUpdates raised %s", type(exc).__name__)
            return []
        if not isinstance(data, dict) or not data.get("ok"):
            log.warning("telegram getUpdates returned a non-ok response")
            return []
        result = data.get("result", [])
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: str, text: str) -> None:
        """Deliver a reply, chunked at the 4096 limit. Never raises."""
        for chunk in chunk_message(text, self._limit):
            payload: dict[str, object] = {"chat_id": chat_id, "text": chunk}
            try:
                status = self._post(f"{self._base}/sendMessage", payload, self._send_timeout)
                if not (200 <= status < 300):
                    log.warning("telegram sendMessage HTTP %s", status)
            except Exception as exc:  # noqa: BLE001 — best-effort reply
                log.warning("telegram sendMessage raised %s", type(exc).__name__)
