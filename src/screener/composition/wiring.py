"""The composition root. The ONLY place adapters are instantiated (architecture §3, rule 3) —
a grep for an adapter or SDK import anywhere else is a build failure. Selecting the backend here
is the entire data-layer half of the RPi migration (PRD §8.5)."""

from __future__ import annotations

from dataclasses import dataclass

from screener.adapters.calendar.xnys_calendar import XnysCalendar
from screener.adapters.clock.system_clock import SystemClock
from screener.adapters.market_data.yfinance_provider import YFinanceProvider
from screener.adapters.notify.console_notifier import ConsoleNotifier
from screener.adapters.notify.telegram_notifier import TelegramNotifier
from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.config import Settings, load_settings
from screener.domain.errors import ConfigError
from screener.ports.calendar import TradingCalendar
from screener.ports.clock import Clock
from screener.ports.market_data import MarketDataProvider
from screener.ports.notifier import Notifier
from screener.ports.repository import ScreenerRepository
from screener.screener.criterion import Criterion, MA150ProximityCriterion
from screener.screener.pipeline import ScanPipeline


@dataclass
class Application:
    settings: Settings
    repo: ScreenerRepository
    provider: MarketDataProvider
    calendar: TradingCalendar
    clock: Clock
    notifier: Notifier
    criteria: list[Criterion]

    def pipeline(self) -> ScanPipeline:
        return ScanPipeline(
            repo=self.repo,
            provider=self.provider,
            calendar=self.calendar,
            clock=self.clock,
            notifier=self.notifier,
            criteria=self.criteria,
            settings=self.settings,
        )


def build_application(settings: Settings | None = None) -> Application:
    cfg = settings or load_settings()

    if cfg.repository_backend == "sqlite":
        repo: ScreenerRepository = SqliteScreenerRepository(cfg.db_path)
    else:  # dynamodb adapter is a later milestone
        raise ConfigError(
            f"repository_backend={cfg.repository_backend!r} is not implemented yet; use 'sqlite'"
        )

    criteria: list[Criterion] = [MA150ProximityCriterion(cfg.band_atr_mult)]

    # Real Telegram delivery when credentials are configured; otherwise the console stand-in
    # keeps CLI dry-runs working without a bot token (FR-5).
    notifier: Notifier
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    else:
        notifier = ConsoleNotifier()

    return Application(
        settings=cfg,
        repo=repo,
        provider=YFinanceProvider(),
        calendar=XnysCalendar(),
        clock=SystemClock(),
        notifier=notifier,
        criteria=criteria,
    )
