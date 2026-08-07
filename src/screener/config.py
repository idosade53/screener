"""One frozen Settings object, constructed in the composition root and injected downward
(architecture §8.2). No module reads the environment directly."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from screener.domain.models import ScanType


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCREENER_", env_file=".env", frozen=True, extra="ignore"
    )

    band_atr_mult: Decimal = Decimal("1.5")  # Q4, global
    sma_period: int = 150
    atr_period: int = 14
    min_bars_required: int = 165  # 150 SMA + 14 ATR seed + 1 (FR §4.4)
    universe_cap: int = 300
    backfill_years: int = 2

    # ET scan times as "HH:MM,HH:MM,HH:MM" for PRE, OPEN, CLOSE (PRD §5).
    scan_times_et: str = "09:00,09:45,20:15"

    alert_combinator: Literal["ALL", "ANY"] = "ALL"

    # Fraction of the universe that must fail before a scan is treated as a provider outage
    # (§7.2) rather than a set of per-symbol failures.
    stale_failure_fraction: float = 0.5

    repository_backend: Literal["sqlite", "dynamodb"] = "sqlite"
    provider: Literal["yfinance"] = "yfinance"
    db_path: str = "screener.db"

    # DynamoDB backend (architecture §9.3 single table). Only read when
    # repository_backend == "dynamodb"; region falls back to the standard AWS env vars.
    dynamodb_table: str = "screener"
    aws_region: str | None = None

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @field_validator("scan_times_et")
    @classmethod
    def _validate_times(cls, v: str) -> str:
        parts = [p.strip() for p in v.split(",")]
        if len(parts) != 3:
            raise ValueError("scan_times_et must be three HH:MM values for PRE, OPEN, CLOSE")
        for p in parts:
            hh, mm = p.split(":")
            time(int(hh), int(mm))  # raises if invalid
        return v

    @property
    def scheduled_times(self) -> dict[ScanType, time]:
        pre, open_, close = (p.strip() for p in self.scan_times_et.split(","))
        return {
            ScanType.PRE: _parse_time(pre),
            ScanType.OPEN: _parse_time(open_),
            ScanType.CLOSE: _parse_time(close),
        }


def _parse_time(hhmm: str) -> time:
    hh, mm = hhmm.split(":")
    return time(int(hh), int(mm))


def load_settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]
