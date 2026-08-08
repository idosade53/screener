"""Lambda handlers (Stage 2) driven with fakes — no AWS, no network. Confirms the scan handler
parses the EventBridge ``scan_type`` and runs the pipeline, and the webhook handler routes an
authorized update to a reply while honouring the secret-token gate and the chat allowlist."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import screener.composition.lambda_scan as lscan
import screener.composition.lambda_webhook as lweb
from screener.config import Settings, load_settings
from screener.domain.errors import ConfigError
from screener.domain.models import ScanStatus, ScanSummary, ScanType

CHAT = "123456"


# ------------------------------------------------------------------- scan handler
class _FakePipeline:
    def __init__(self) -> None:
        self.ran: list[ScanType] = []

    def run(self, scan_type: ScanType) -> ScanSummary:
        self.ran.append(scan_type)
        now = datetime(2026, 8, 6, 20, 15, tzinfo=UTC)
        return ScanSummary(
            scan_id=f"2026-08-06T20:15Z#{scan_type.value}",
            scan_type=scan_type,
            scheduled_at=now,
            ran_at=now,
            trading_day=date(2026, 8, 6),
            status=ScanStatus.OK,
            symbols_scanned=1,
            in_range=("AAPL",),
            error_symbols=(),
            insufficient_symbols=(),
        )


class _FakeApp:
    def __init__(self, pipeline: _FakePipeline) -> None:
        self._pipeline = pipeline

    def pipeline(self) -> _FakePipeline:
        return self._pipeline


@pytest.fixture
def scan_pipeline(monkeypatch: pytest.MonkeyPatch) -> _FakePipeline:
    pipeline = _FakePipeline()
    monkeypatch.setattr(lscan, "_app", None)
    monkeypatch.setattr(lscan, "resolve_settings", lambda: load_settings())
    monkeypatch.setattr(lscan, "build_application", lambda _s: _FakeApp(pipeline))
    return pipeline


def test_scan_handler_runs_requested_type(scan_pipeline: _FakePipeline) -> None:
    out = lscan.handler({"scan_type": "close"})
    assert scan_pipeline.ran == [ScanType.CLOSE]
    assert out["scan_id"].endswith("#CLOSE")
    assert out["in_range"] == ["AAPL"]


def test_scan_handler_rejects_unknown_type(scan_pipeline: _FakePipeline) -> None:
    with pytest.raises(ConfigError):
        lscan.handler({"scan_type": "NOPE"})


def test_scan_handler_requires_type(scan_pipeline: _FakePipeline) -> None:
    with pytest.raises(ConfigError):
        lscan.handler({})


# ---------------------------------------------------------------- webhook handler
class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _event(update: dict[str, Any], *, secret_header: str | None = None) -> dict[str, Any]:
    ev: dict[str, Any] = {"body": json.dumps(update)}
    if secret_header is not None:
        ev["headers"] = {"x-telegram-bot-api-secret-token": secret_header}
    return ev


def _update(chat_id: str, text: str) -> dict[str, Any]:
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def webhook(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeTransport:
    transport = _FakeTransport()

    def _settings(**extra: Any) -> Settings:
        return load_settings(
            db_path=str(tmp_path / "s.db"),
            telegram_chat_id=CHAT,
            telegram_bot_token="TOKEN",
            **extra,
        )

    monkeypatch.setattr(lweb, "_app", None)
    monkeypatch.setattr(lweb, "resolve_settings", _settings)
    monkeypatch.setattr(lweb, "_build_transport", lambda _s: transport)
    # Store the factory on the fixture object so a test can re-point it with a secret.
    transport._settings = _settings  # type: ignore[attr-defined]
    return transport


def test_webhook_replies_to_authorized_update(webhook: _FakeTransport) -> None:
    resp = lweb.handler(_event(_update(CHAT, "/list")))
    assert resp["statusCode"] == 200
    assert len(webhook.sent) == 1
    assert webhook.sent[0][0] == CHAT


def test_webhook_ignores_unauthorized_chat(webhook: _FakeTransport) -> None:
    resp = lweb.handler(_event(_update("999", "/list")))
    assert resp["statusCode"] == 200
    assert webhook.sent == []


def test_webhook_rejects_bad_secret(
    monkeypatch: pytest.MonkeyPatch, webhook: _FakeTransport
) -> None:
    settings_factory = webhook._settings  # type: ignore[attr-defined]
    monkeypatch.setattr(
        lweb, "resolve_settings", lambda: settings_factory(telegram_webhook_secret="s3cret")
    )
    monkeypatch.setattr(lweb, "_app", None)
    resp = lweb.handler(_event(_update(CHAT, "/list"), secret_header="wrong"))
    assert resp["statusCode"] == 200
    assert webhook.sent == []


def test_webhook_accepts_matching_secret(
    monkeypatch: pytest.MonkeyPatch, webhook: _FakeTransport
) -> None:
    settings_factory = webhook._settings  # type: ignore[attr-defined]
    monkeypatch.setattr(
        lweb, "resolve_settings", lambda: settings_factory(telegram_webhook_secret="s3cret")
    )
    monkeypatch.setattr(lweb, "_app", None)
    resp = lweb.handler(_event(_update(CHAT, "/list"), secret_header="s3cret"))
    assert resp["statusCode"] == 200
    assert len(webhook.sent) == 1
