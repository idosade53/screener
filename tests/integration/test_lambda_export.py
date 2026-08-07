"""End-to-end export (Stage 2): seed a moto DynamoDB table via the real adapter, run the export
handler, then reopen the S3-uploaded SQLite file through the SQLite adapter and confirm the data
round-tripped. No network (§10)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import screener.composition.lambda_export as lexport
from screener.adapters.repository.dynamodb_repository import DynamoDbScreenerRepository
from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.config import load_settings
from screener.domain.models import (
    Bar,
    Indicators,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    SymbolStatus,
)

_TABLE = "screener-test"
_BUCKET = "screener-exports"
_REGION = "us-east-1"


def _bar(d: str, close: str) -> Bar:
    c = Decimal(close)
    return Bar(date=date.fromisoformat(d), open=c, high=c, low=c, close=c, volume=1000)


def _seed(repo: DynamoDbScreenerRepository) -> None:
    repo.add_symbols(["AAPL", "KO"])
    repo.remove_symbol("KO")  # soft-deleted; must not appear in the export universe
    repo.upsert_bars("AAPL", [_bar("2026-08-05", "100"), _bar("2026-08-06", "101")])
    asof = date(2026, 8, 6)
    repo.put_indicators({"AAPL": Indicators(Decimal("99.5"), Decimal("3.1"), asof)})
    now = datetime(2026, 8, 6, 20, 15, tzinfo=UTC)
    summary = ScanSummary(
        scan_id="2026-08-06T20:15Z#CLOSE",
        scan_type=ScanType.CLOSE,
        scheduled_at=now,
        ran_at=now,
        trading_day=asof,
        status=ScanStatus.OK,
        symbols_scanned=1,
        in_range=("AAPL",),
        error_symbols=(),
        insufficient_symbols=(),
    )
    result = SymbolScanResult(
        symbol="AAPL",
        status=SymbolStatus.OK,
        price=Decimal("101.00"),
        indicators=Indicators(Decimal("99.5"), Decimal("3.1"), asof),
        distance_atr=Decimal("0.48"),
        in_range=True,
    )
    repo.save_scan(summary, [result])


def test_export_round_trips_through_s3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(var, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=_REGION)
        dynamodb.create_table(
            TableName=_TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        table = dynamodb.Table(_TABLE)
        table.wait_until_exists()
        _seed(DynamoDbScreenerRepository(table))

        s3 = boto3.client("s3", region_name=_REGION)
        s3.create_bucket(Bucket=_BUCKET)

        settings = load_settings(
            repository_backend="dynamodb",
            dynamodb_table=_TABLE,
            export_bucket=_BUCKET,
            aws_region=_REGION,
        )
        monkeypatch.setattr(lexport, "resolve_settings", lambda: settings)
        monkeypatch.setattr(lexport, "_LOCAL_DB", str(tmp_path / "screener.db"))

        out = lexport.handler({})
        assert out["items"] > 0

        # Only the stable key should remain (the temp key was copied then deleted).
        keys = {o["Key"] for o in s3.list_objects_v2(Bucket=_BUCKET).get("Contents", [])}
        assert keys == {"screener-latest.db"}

        local = tmp_path / "downloaded.db"
        s3.download_file(_BUCKET, "screener-latest.db", str(local))

    # Reopen the exported analytical copy outside the mock and verify fidelity.
    exported = SqliteScreenerRepository(str(local))
    try:
        assert {m.symbol for m in exported.get_universe()} == {"AAPL"}  # KO soft-deleted
        assert [b.close for b in exported.get_bars("AAPL", since=date(2026, 8, 1))] == [
            Decimal("100"),
            Decimal("101"),
        ]
        assert exported.get_indicators(["AAPL"], asof=date(2026, 8, 6))["AAPL"].sma150 == Decimal(
            "99.5"
        )
        latest = exported.latest_scan()
        assert latest is not None and latest.in_range == ("AAPL",)
    finally:
        exported.close()
