"""JSON (de)serialisation for the Phase-4 fundamentals/news cache, shared by the SQLite and
DynamoDB repository adapters so both stores stay byte-for-byte behaviourally identical (PRD FR-6).

The derived scored metrics are persisted as a compact JSON payload (PRD §10), not raw statements.
``Decimal``/``date``/``datetime`` leaves are tagged so they round-trip exactly (numeric policy,
ADR A11) rather than degrading to float."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from screener.domain.models import (
    CachedFundamentals,
    CompanyProfile,
    FundamentalsSnapshot,
    NewsItem,
)


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return {"$dec": str(o)}
        if isinstance(o, datetime):  # subclass of date -> check first
            return {"$dt": o.isoformat()}
        if isinstance(o, date):
            return {"$d": o.isoformat()}
        return super().default(o)


def _revive(obj: dict[str, Any]) -> Any:
    if "$dec" in obj:
        return Decimal(obj["$dec"])
    if "$dt" in obj:
        return datetime.fromisoformat(obj["$dt"])
    if "$d" in obj:
        return date.fromisoformat(obj["$d"])
    return obj


def dumps_cached_fundamentals(cached: CachedFundamentals) -> str:
    return json.dumps(
        {"profile": asdict(cached.profile), "snapshot": asdict(cached.snapshot)}, cls=_Encoder
    )


def loads_cached_fundamentals(payload: str) -> CachedFundamentals:
    raw = json.loads(payload, object_hook=_revive)
    return CachedFundamentals(
        profile=CompanyProfile(**raw["profile"]),
        snapshot=FundamentalsSnapshot(**raw["snapshot"]),
    )


def dumps_news_items(items: tuple[NewsItem, ...]) -> str:
    return json.dumps([asdict(i) for i in items], cls=_Encoder)


def loads_news_items(payload: str) -> tuple[NewsItem, ...]:
    raw = json.loads(payload, object_hook=_revive)
    return tuple(NewsItem(**item) for item in raw)
