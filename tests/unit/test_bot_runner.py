"""M7T03 poll-loop wiring: raw Telegram update dicts → dispatch → replies, driven by a fake
transport (no network). Confirms authorized commands get a reply, unauthorized chats stay
silent, and the offset acks each update so it isn't re-processed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.bot.context import BotContext
from screener.composition.bot_runner import poll_loop
from screener.config import Settings, load_settings

CHAT = "123456"


def _update(update_id: int, chat_id: str, text: str) -> dict[str, Any]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


class FakeTransport:
    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self._batches = list(batches)
        self.sent: list[tuple[str, str]] = []
        self.offsets: list[int | None] = []

    def get_updates(self, offset: int | None, poll_timeout: int) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        return self._batches.pop(0) if self._batches else []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(db_path=str(tmp_path / "s.db"), telegram_chat_id=CHAT)


@pytest.fixture
def ctx(settings: Settings) -> BotContext:
    repo = SqliteScreenerRepository(settings.db_path)
    return BotContext(
        repo=repo,
        settings=settings,
        run_scan=lambda: None,  # type: ignore[arg-type,return-value]
        build_dossier=lambda _s, _ai=None: None,  # type: ignore[arg-type,return-value]
    )


def test_authorized_command_gets_a_reply(ctx: BotContext) -> None:
    transport = FakeTransport([[_update(10, CHAT, "/add aapl")]])
    poll_loop(ctx, transport, max_batches=1)
    assert len(transport.sent) == 1
    chat_id, reply = transport.sent[0]
    assert chat_id == CHAT and "Added" in reply
    assert {m.symbol for m in ctx.repo.get_universe()} == {"AAPL"}


def test_unauthorized_chat_is_silent(ctx: BotContext) -> None:
    transport = FakeTransport([[_update(10, "999", "/add aapl")]])
    poll_loop(ctx, transport, max_batches=1)
    assert transport.sent == []


def test_offset_acks_processed_update(ctx: BotContext) -> None:
    transport = FakeTransport([[_update(42, CHAT, "/list")], []])
    poll_loop(ctx, transport, max_batches=2)
    # First poll starts at offset=None; the second must ask for update_id+1 so 42 isn't re-sent.
    assert transport.offsets == [None, 43]
