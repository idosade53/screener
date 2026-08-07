"""Alert delivery. Chunking and retries are the adapter's job; a failed alert must never
crash the scheduler (FR-5)."""

from __future__ import annotations

from typing import Protocol

from screener.domain.models import DeliveryStatus


class Notifier(Protocol):
    def send(self, message: str) -> DeliveryStatus:
        """Chunking at 4096 chars is the adapter's job. Retries 3× internally.
        MUST NOT raise — a failed alert never crashes the scheduler (FR-5)."""
        ...
