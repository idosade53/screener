"""``screener-export`` Lambda handler (architecture §9.4). After each successful ``CLOSE`` it
materialises the whole DynamoDB table into the PRD FR-6 SQLite schema and uploads it to S3 — the
analytical copy Phase 2/3 read with real SQL, and the §8.5 "path 2" RPi migration seed.

A *full rebuild*, not an incremental sync (ADR A9): at ~20 MB/year the rebuild is trivial and it
removes an entire class of sync bug. Written to a temp key then copied to the stable key so a
reader never sees a half-written file. This module is inherently DynamoDB-specific — it is the one
place that reads the table by scan rather than by access pattern.

Fidelity note: universe ``added_at``/``last_validated_at`` and per-observation indicator ``asof``
are not columns in the FR-6 analytical schema, so they are not carried across; every field Phase 2
consumes (bars, indicators, prices, distances, in-range history) is preserved exactly."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date
from typing import Any

import boto3

from screener.adapters.repository.dynamodb_repository import _dec, _summary_from_item
from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.composition.secrets import resolve_settings
from screener.config import Settings
from screener.domain.errors import ConfigError
from screener.domain.models import Bar, DeliveryStatus, Indicators, SymbolScanResult, SymbolStatus

log = logging.getLogger("screener.lambda.export")

_LOCAL_DB = "/tmp/screener.db"  # Lambda's only writable path.


def handler(event: dict[str, Any] | None = None, context: object = None) -> dict[str, Any]:
    settings = resolve_settings()
    if settings.repository_backend != "dynamodb":
        raise ConfigError("export requires the dynamodb backend")
    if not settings.export_bucket:
        raise ConfigError("export_bucket must be configured for the export Lambda")

    items = _scan_table(settings)
    _rebuild_sqlite(items, _LOCAL_DB)
    _upload(settings, _LOCAL_DB)
    log.info(
        "exported %d items to s3://%s/%s", len(items), settings.export_bucket, settings.export_key
    )
    return {"items": len(items), "bucket": settings.export_bucket, "key": settings.export_key}


def _scan_table(settings: Settings) -> list[dict[str, Any]]:
    dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    table = dynamodb.Table(settings.dynamodb_table)
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        start = resp.get("LastEvaluatedKey")
        if not start:
            return items
        kwargs["ExclusiveStartKey"] = start


def _rebuild_sqlite(items: list[dict[str, Any]], path: str) -> None:
    if os.path.exists(path):
        os.remove(path)  # full rebuild — never append to a stale file
    repo = SqliteScreenerRepository(path)
    try:
        _load_universe(repo, items)
        _load_bars(repo, items)
        _load_indicators(repo, items)
        _load_scans(repo, items)
        _load_alerts(repo, items)
    finally:
        repo.close()


def _load_universe(repo: SqliteScreenerRepository, items: list[dict[str, Any]]) -> None:
    for it in items:
        if it["PK"] != "UNIVERSE":
            continue
        sym = it["sym"]
        repo.add_symbols([sym])
        if not bool(it.get("active", True)):
            repo.remove_symbol(sym)  # preserve the soft-delete flag (ADR A12)


def _load_bars(repo: SqliteScreenerRepository, items: list[dict[str, Any]]) -> None:
    by_symbol: dict[str, list[Bar]] = defaultdict(list)
    for it in items:
        pk = it["PK"]
        if not pk.startswith("BAR#"):
            continue
        by_symbol[pk[4:]].append(
            Bar(
                date=date.fromisoformat(it["SK"]),
                open=_dec(it["o"]),
                high=_dec(it["h"]),
                low=_dec(it["l"]),
                close=_dec(it["c"]),
                volume=int(it["v"]),
            )
        )
    for sym, bars in by_symbol.items():
        repo.upsert_bars(sym, bars)


def _load_indicators(repo: SqliteScreenerRepository, items: list[dict[str, Any]]) -> None:
    values: dict[str, Indicators] = {}
    for it in items:
        pk = it["PK"]
        if not pk.startswith("IND#"):
            continue
        values[pk[4:]] = Indicators(
            sma150=_dec(it["sma"]), atr14=_dec(it["atr"]), asof=date.fromisoformat(it["asof"])
        )
    if values:
        repo.put_indicators(values)


def _load_scans(repo: SqliteScreenerRepository, items: list[dict[str, Any]]) -> None:
    # Observations (HIST#<sym>) are keyed by the scan's ran_at, which is also stored on the SCAN
    # summary — join on it to reattach each scan's per-symbol results.
    observations: dict[str, list[SymbolScanResult]] = defaultdict(list)
    for it in items:
        pk = it["PK"]
        if not pk.startswith("HIST#"):
            continue
        observations[it["SK"]].append(_observation(pk[5:], it))
    for it in items:
        if it["PK"] != "SCAN":
            continue
        summary = _summary_from_item(it)
        repo.save_scan(summary, observations.get(it["ran"], []))


def _observation(symbol: str, it: dict[str, Any]) -> SymbolScanResult:
    indicators = (
        Indicators(sma150=_dec(it["sma"]), atr14=_dec(it["atr"]), asof=date.min)
        if "sma" in it and "atr" in it
        else None
    )
    return SymbolScanResult(
        symbol=symbol,
        status=SymbolStatus(it["status"]),
        price=_dec(it["p"]) if "p" in it else None,
        indicators=indicators,
        distance_atr=_dec(it["dist"]) if "dist" in it else None,
        in_range=bool(it["in"]),
    )


def _load_alerts(repo: SqliteScreenerRepository, items: list[dict[str, Any]]) -> None:
    for it in items:
        pk = it["PK"]
        if not pk.startswith("ALERT#"):
            continue
        repo.record_alert(pk[6:], it["msg"], DeliveryStatus(it["status"]))


def _upload(settings: Settings, path: str) -> None:
    s3 = boto3.client("s3", region_name=settings.aws_region)
    bucket = settings.export_bucket
    final_key = settings.export_key
    temp_key = f"{final_key}.tmp"
    with open(path, "rb") as fh:
        s3.put_object(Bucket=bucket, Key=temp_key, Body=fh.read())
    # Atomic swap: readers only ever see the fully-written object (§9.4).
    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": temp_key}, Key=final_key)
    s3.delete_object(Bucket=bucket, Key=temp_key)
