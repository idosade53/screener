"""Optional AI-summary stage behind a port (Claude Haiku primary — PRD §8, FR-7). Off by
default; the whole dossier stays free and deterministic without it. The provider is fed only
the already-fetched structured data on the ``Dossier`` (no extra network fetch), so it is
fully mockable in tests. Like the other ports, it must never raise past the adapter."""

from __future__ import annotations

from typing import Protocol

from screener.domain.models import Dossier


class SummaryProvider(Protocol):
    def summarize(self, dossier: Dossier) -> str:
        """One-paragraph plain-English synthesis of the scorecard + fundamentals + news.
        Called with a ``Dossier`` whose ``ai_summary`` is ``None``; the assembler folds the
        result back in via ``dataclasses.replace`` (FR-7)."""
        ...
