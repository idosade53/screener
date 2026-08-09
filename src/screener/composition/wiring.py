"""The composition root. The ONLY place adapters are instantiated (architecture §3, rule 3) —
a grep for an adapter or SDK import anywhere else is a build failure. Selecting the backend here
is the entire data-layer half of the RPi migration (PRD §8.5)."""

from __future__ import annotations

from dataclasses import dataclass

from screener.adapters.calendar.xnys_calendar import XnysCalendar
from screener.adapters.clock.system_clock import SystemClock
from screener.adapters.fundamentals.fmp_provider import FmpFundamentalsProvider
from screener.adapters.fundamentals.yfinance_provider import YFinanceFundamentalsProvider
from screener.adapters.market_data.yfinance_provider import YFinanceProvider
from screener.adapters.news.alphavantage_provider import AlphaVantageNewsProvider
from screener.adapters.news.finnhub_provider import FinnhubNewsProvider
from screener.adapters.news.yfinance_provider import YFinanceNewsProvider
from screener.adapters.notify.console_notifier import ConsoleNotifier
from screener.adapters.notify.telegram_notifier import TelegramNotifier
from screener.adapters.repository.dynamodb_repository import DynamoDbScreenerRepository
from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.adapters.summary.anthropic_provider import AnthropicSummaryProvider
from screener.bot.context import BotContext
from screener.config import Settings, load_settings
from screener.domain.errors import ConfigError
from screener.domain.models import Dossier, ScanType
from screener.fundamentals.dossier import DossierService
from screener.fundamentals.thresholds import ScorecardThresholds
from screener.ports.calendar import TradingCalendar
from screener.ports.clock import Clock
from screener.ports.fundamentals import FundamentalsProvider
from screener.ports.market_data import MarketDataProvider
from screener.ports.news import NewsProvider
from screener.ports.notifier import Notifier
from screener.ports.repository import ScreenerRepository
from screener.ports.summary import SummaryProvider
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
    dossier_service: DossierService

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

    def build_dossier(
        self, symbol: str, *, force_refresh: bool = False, with_ai: bool = False
    ) -> Dossier:
        return self.dossier_service.build(symbol, force_refresh=force_refresh, with_ai=with_ai)


def build_application(settings: Settings | None = None) -> Application:
    cfg = settings or load_settings()

    repo: ScreenerRepository
    if cfg.repository_backend == "sqlite":
        repo = SqliteScreenerRepository(cfg.db_path)
    elif cfg.repository_backend == "dynamodb":
        import boto3  # local import: keep boto3 off the SQLite/CLI path

        table = boto3.resource("dynamodb", region_name=cfg.aws_region).Table(cfg.dynamodb_table)
        repo = DynamoDbScreenerRepository(table)
    else:  # unreachable given the Literal, but keeps the failure explicit
        raise ConfigError(f"unknown repository_backend={cfg.repository_backend!r}")

    criteria: list[Criterion] = [MA150ProximityCriterion(cfg.band_atr_mult)]

    # Real Telegram delivery when credentials are configured; otherwise the console stand-in
    # keeps CLI dry-runs working without a bot token (FR-5).
    notifier: Notifier
    if cfg.telegram_bot_token and cfg.telegram_chat_id:
        notifier = TelegramNotifier(cfg.telegram_bot_token, cfg.telegram_chat_id)
    else:
        notifier = ConsoleNotifier()

    clock = SystemClock()
    fundamentals, fundamentals_fallback = _build_fundamentals(cfg)
    news, news_fallback = _build_news(cfg)
    dossier_service = DossierService(
        repo=repo,
        fundamentals=fundamentals,
        fundamentals_fallback=fundamentals_fallback,
        news=news,
        news_fallback=news_fallback,
        clock=clock,
        thresholds=ScorecardThresholds.default(),
        summary=_build_summary(cfg),
        fundamentals_cache_days=cfg.fundamentals_cache_days,
        news_cache_hours=cfg.news_cache_hours,
        news_lookback_days=cfg.news_lookback_days,
    )

    return Application(
        settings=cfg,
        repo=repo,
        provider=YFinanceProvider(),
        calendar=XnysCalendar(),
        clock=clock,
        notifier=notifier,
        criteria=criteria,
        dossier_service=dossier_service,
    )


def _build_fundamentals(
    cfg: Settings,
) -> tuple[FundamentalsProvider, FundamentalsProvider | None]:
    """FMP primary with a yfinance fallback (PRD §8). Falls back to yfinance-only when no FMP key
    is configured or yfinance is explicitly selected."""
    yfin = YFinanceFundamentalsProvider()
    if cfg.fundamentals_provider == "fmp" and cfg.fmp_api_key:
        return FmpFundamentalsProvider(cfg.fmp_api_key), yfin
    return yfin, None


def _build_summary(cfg: Settings) -> SummaryProvider | None:
    """The optional AI stage (F6). Built only when an Anthropic key is configured; whether it
    actually runs is still gated per-call by ``with_ai`` (CLI ``--ai`` / ``dossier_ai_summary``)."""
    if not cfg.anthropic_api_key:
        return None
    return AnthropicSummaryProvider(cfg.anthropic_api_key)


def _build_news(cfg: Settings) -> tuple[NewsProvider, NewsProvider | None]:
    """Selected news primary with a yfinance ``.news`` fallback (PRD §8, F7). Alpha Vantage or
    Finnhub when its key is set; otherwise yfinance-only."""
    yfin = YFinanceNewsProvider(max_items=cfg.news_max_items)
    if cfg.news_provider == "alphavantage" and cfg.alphavantage_api_key:
        return (
            AlphaVantageNewsProvider(cfg.alphavantage_api_key, max_items=cfg.news_max_items),
            yfin,
        )
    if cfg.news_provider == "finnhub" and cfg.finnhub_api_key:
        return (
            FinnhubNewsProvider(cfg.finnhub_api_key, max_items=cfg.news_max_items),
            yfin,
        )
    return yfin, None


def build_bot_context(app: Application) -> BotContext:
    """Narrow the composition-root ``Application`` down to what a bot handler may touch
    (M5). This is the seam the M7 long-poll loop injects into ``bot.dispatch.dispatch``."""
    return BotContext(
        repo=app.repo,
        settings=app.settings,
        run_scan=lambda: app.pipeline().run(ScanType.MANUAL),
        build_dossier=lambda symbol: app.build_dossier(
            symbol, with_ai=app.settings.dossier_ai_summary
        ),
    )
