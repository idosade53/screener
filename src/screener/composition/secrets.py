"""Settings resolution for the Lambda target (architecture §9.5, "Settings loader only").

On Lambda the Telegram secrets live in SSM Parameter Store, not a ``.env``: when
``SCREENER_SSM_PREFIX`` is set, fetch them and pass them as overrides to the normal loader.
Everywhere else (CLI, RPi, tests) this is a plain :func:`load_settings`, so boto3 never loads
outside the Lambda path. Keeping this in the composition layer keeps the SSM SDK out of core."""

from __future__ import annotations

import logging
import os

from screener.config import Settings, load_settings

log = logging.getLogger("screener.secrets")

# SSM parameter basename -> Settings field.
_SECRET_PARAMS = {
    "telegram_bot_token": "telegram_bot_token",
    "telegram_chat_id": "telegram_chat_id",
    "telegram_webhook_secret": "telegram_webhook_secret",
}


def resolve_settings() -> Settings:
    """Load settings, overlaying SSM secrets when ``SCREENER_SSM_PREFIX`` is configured."""
    prefix = os.environ.get("SCREENER_SSM_PREFIX")
    if not prefix:
        return load_settings()
    return load_settings(**_fetch_ssm_secrets(prefix))


def _fetch_ssm_secrets(prefix: str) -> dict[str, str]:
    import boto3

    ssm = boto3.client("ssm")
    names = {f"{prefix.rstrip('/')}/{name}": field for name, field in _SECRET_PARAMS.items()}
    resp = ssm.get_parameters(Names=list(names), WithDecryption=True)
    overrides = {names[p["Name"]]: p["Value"] for p in resp.get("Parameters", [])}
    missing = set(_SECRET_PARAMS.values()) - set(overrides)
    if missing:
        # Not fatal: the webhook secret is optional, and a bad token surfaces at send time.
        log.warning("SSM prefix %s missing secrets: %s", prefix, sorted(missing))
    return overrides
