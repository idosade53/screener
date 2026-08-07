"""Notifiers that don't touch Telegram — used by the CLI (M3) until the real Telegram adapter
lands (M4). A Notifier MUST NOT raise (FR-5)."""

from __future__ import annotations

from screener.domain.models import DeliveryStatus


class ConsoleNotifier:
    """Prints the digest to stdout and reports SENT."""

    def send(self, message: str) -> DeliveryStatus:
        print(message)
        return DeliveryStatus.SENT


class CollectingNotifier:
    """Captures messages in memory for tests and dry runs; never raises."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> DeliveryStatus:
        self.messages.append(message)
        return DeliveryStatus.SENT
