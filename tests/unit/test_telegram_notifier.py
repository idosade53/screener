"""Unit tests for the Telegram transport (M4T05/M4T06). No network: the HTTP POST and the
sleep are injected, so chunking, retry, and the never-raise contract are exercised directly."""

from __future__ import annotations

import logging

import httpx

from screener.adapters.notify.telegram_notifier import (
    TelegramNotifier,
    chunk_message,
)
from screener.domain.models import DeliveryStatus


class RecordingPost:
    """Fake ``PostFn`` returning a scripted sequence of status codes (last value repeats)."""

    def __init__(self, statuses: list[int]) -> None:
        self._statuses = statuses
        self.calls: list[tuple[str, dict[str, object], float]] = []

    def __call__(self, url: str, payload: dict[str, object], timeout: float) -> int:
        self.calls.append((url, payload, timeout))
        idx = min(len(self.calls) - 1, len(self._statuses) - 1)
        return self._statuses[idx]


def _notifier(post: object, **kw: object) -> TelegramNotifier:
    return TelegramNotifier(
        "TOKEN", "CHAT", post=post, sleep=lambda _s: None, backoff=0.0, **kw  # type: ignore[arg-type]
    )


# ---- chunking (M4T06) ----

def test_short_message_is_one_chunk() -> None:
    assert chunk_message("hello", limit=4096) == ["hello"]


def test_splits_on_line_boundaries_without_cutting_rows() -> None:
    lines = [f"row-{i:03d}" for i in range(1000)]  # 8000 chars, well over 4096
    message = "\n".join(lines)
    chunks = chunk_message(message, limit=4096)

    assert len(chunks) >= 2
    assert all(len(c) <= 4096 for c in chunks)
    # No row was split across a boundary, and reassembly is lossless.
    assert "\n".join(chunks).split("\n") == lines


def test_single_overlong_line_is_hard_split() -> None:
    line = "x" * 9000
    chunks = chunk_message(line, limit=4096)
    assert [len(c) for c in chunks] == [4096, 4096, 808]
    assert "".join(chunks) == line


# ---- delivery + retry (M4T05) ----

def test_send_success_posts_plain_text_payload() -> None:
    post = RecordingPost([200])
    status = _notifier(post).send("digest body")

    assert status is DeliveryStatus.SENT
    assert len(post.calls) == 1
    url, payload, _timeout = post.calls[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload == {"chat_id": "CHAT", "text": "digest body"}
    assert "parse_mode" not in payload


def test_retries_then_succeeds() -> None:
    post = RecordingPost([500, 500, 200])
    status = _notifier(post, max_retries=3).send("hi")

    assert status is DeliveryStatus.SENT
    assert len(post.calls) == 3  # two failures, third succeeds


def test_gives_up_after_max_retries_returns_failed() -> None:
    post = RecordingPost([500])
    status = _notifier(post, max_retries=3).send("hi")

    assert status is DeliveryStatus.FAILED
    assert len(post.calls) == 3


def test_never_raises_when_post_throws() -> None:
    def boom(url: str, payload: dict[str, object], timeout: float) -> int:
        raise RuntimeError("network down")

    status = _notifier(boom, max_retries=2).send("hi")
    assert status is DeliveryStatus.FAILED


def test_multi_chunk_all_delivered_is_sent() -> None:
    post = RecordingPost([200])
    message = "\n".join(f"row-{i:03d}" for i in range(1000))
    status = _notifier(post).send(message)

    assert status is DeliveryStatus.SENT
    assert len(post.calls) == len(chunk_message(message))


# ---- secret hygiene ----

def test_token_never_appears_in_logs_on_failure(caplog) -> None:  # type: ignore[no-untyped-def]
    """httpx errors can carry the request URL, which embeds the bot token in its path. The
    notifier must log exception *types* only — never the exception object or traceback."""
    token = "123456:AAABBBsecrettokenshouldnotleak"

    def boom(url: str, payload: dict[str, object], timeout: float) -> int:
        raise httpx.ConnectError(f"failed connecting to {url}")  # url embeds the token

    notifier = TelegramNotifier(
        token, "CHAT", post=boom, sleep=lambda _s: None, backoff=0.0, max_retries=2
    )
    with caplog.at_level(logging.DEBUG, logger="screener.telegram"):
        status = notifier.send("hi")

    assert status is DeliveryStatus.FAILED
    assert caplog.records, "expected the failure to be logged"
    assert token not in caplog.text  # the token must not leak, even at DEBUG
    assert "ConnectError" in caplog.text  # type is logged, so the failure stays diagnosable
