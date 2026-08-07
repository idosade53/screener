"""Dependencies a bot handler is allowed to touch (M5). Deliberately narrower than the
composition-root ``Application``: the bot layer sits *below* ``composition`` (see the
import-linter ``layers`` contract), so it may only depend on ports/domain/config, never on the
composition root or adapters. The composition root builds one of these and injects it inward
(see ``composition/wiring.build_bot_context``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from screener.config import Settings
from screener.domain.models import ScanSummary
from screener.ports.repository import ScreenerRepository


@dataclass(frozen=True)
class BotContext:
    repo: ScreenerRepository
    settings: Settings
    run_scan: Callable[[], ScanSummary]  # triggers a MANUAL scan; the pipeline sends its own digest
