"""Dependencies a bot handler is allowed to touch (M5). Deliberately narrower than the
composition-root ``Application``: the bot layer sits *below* ``composition`` (see the
import-linter ``layers`` contract), so it may only depend on ports/domain/config, never on the
composition root or adapters. The composition root builds one of these and injects it inward
(see ``composition/wiring.build_bot_context``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from screener.config import Settings
from screener.domain.models import Dossier, ScanSummary
from screener.ports.repository import ScreenerRepository


@dataclass(frozen=True)
class BotContext:
    repo: ScreenerRepository
    settings: Settings
    run_scan: Callable[[], ScanSummary]  # triggers a MANUAL scan; the pipeline sends its own digest
    # Builds an on-demand fundamentals dossier (F5). Injected by the composition root so the bot
    # layer stays clear of adapters; may raise UnknownSymbolError for a friendly rejection.
    # ``with_ai`` overrides the AI-summary stage for this call (``/dossier --ai`` / ``--no-ai``);
    # ``None`` falls back to the ``dossier_ai_summary`` setting.
    build_dossier: Callable[[str, bool | None], Dossier]
