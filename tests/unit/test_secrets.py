"""Lambda settings resolution (Stage 2). Without SCREENER_SSM_PREFIX it's a plain loader; with
it, Telegram secrets are overlaid from SSM Parameter Store (moto-backed, no network)."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from screener.composition.secrets import resolve_settings

_REGION = "us-east-1"


def test_resolve_settings_without_prefix_is_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCREENER_SSM_PREFIX", raising=False)
    monkeypatch.setenv("SCREENER_TELEGRAM_BOT_TOKEN", "from-env")
    settings = resolve_settings()
    assert settings.telegram_bot_token == "from-env"


def test_resolve_settings_overlays_ssm_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(var, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    monkeypatch.setenv("SCREENER_SSM_PREFIX", "/screener")
    with mock_aws():
        ssm = boto3.client("ssm", region_name=_REGION)
        ssm.put_parameter(
            Name="/screener/telegram_bot_token", Value="ssm-token", Type="SecureString"
        )
        ssm.put_parameter(Name="/screener/telegram_chat_id", Value="4242", Type="String")
        settings = resolve_settings()
    assert settings.telegram_bot_token == "ssm-token"
    assert settings.telegram_chat_id == "4242"
