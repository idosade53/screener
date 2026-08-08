"""``screener-webhook`` Lambda handler (architecture §9.1) — the Lambda counterpart to the RPi
long-poll loop in :mod:`screener.composition.bot_runner`. Telegram POSTs one ``Update`` to the
Function URL; we parse it, run the same :func:`screener.bot.dispatch.dispatch` seam (via the
already-parsing :func:`bot_runner._process`), and send the reply. Always returns HTTP 200 — a
non-200 makes Telegram retry the delivery, which we never want.

Defence in depth: dispatch already ignores any chat that isn't the configured operator (M5T02);
when a webhook secret is configured we additionally reject requests whose
``X-Telegram-Bot-Api-Secret-Token`` header doesn't match, so the Function URL isn't an open
trigger."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from screener.composition.bot_runner import _process
from screener.composition.secrets import resolve_settings
from screener.composition.wiring import Application, build_application, build_bot_context
from screener.config import Settings

log = logging.getLogger("screener.lambda.webhook")

_OK = {"statusCode": 200, "body": ""}

# Warm-container reuse.
_app: Application | None = None


def _build_transport(settings: Settings) -> Any:
    # Imported lazily and behind a function so tests can inject a fake transport.
    from screener.adapters.notify.telegram_transport import TelegramTransport

    assert settings.telegram_bot_token is not None  # guarded by the caller
    return TelegramTransport(settings.telegram_bot_token)


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers") or {}
    return headers.get(name) or headers.get(name.lower())


def _parse_update(event: dict[str, Any]) -> dict[str, Any] | None:
    body = event.get("body")
    if body is None:
        return None
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        log.warning("webhook body was not valid JSON")
        return None
    return parsed if isinstance(parsed, dict) else None


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    global _app
    if _app is None:
        _app = build_application(resolve_settings())
    settings = _app.settings

    expected = settings.telegram_webhook_secret
    if expected and _header(event or {}, "X-Telegram-Bot-Api-Secret-Token") != expected:
        log.warning("webhook secret mismatch; ignoring request")
        return _OK

    update = _parse_update(event or {})
    if update is None:
        return _OK

    processed = _process(update, build_bot_context(_app))
    if processed is not None:
        chat_id, reply = processed
        if settings.telegram_bot_token:
            _build_transport(settings).send_message(chat_id, reply)
        else:
            log.warning("no telegram_bot_token configured; cannot reply")
    return _OK
