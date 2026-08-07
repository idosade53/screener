"""DynamoDB adapter vs the shared ``RepositoryContract`` (architecture §5, §10). moto's
``mock_aws`` provides an in-process DynamoDB — no Java, no network (§10 "no network in CI"). The
adapter is trustworthy iff it passes the *same* suite as the SQLite adapter; this file adds no new
assertions, only the fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from screener.adapters.repository.dynamodb_repository import DynamoDbScreenerRepository
from screener.ports.repository import ScreenerRepository
from tests.contract.repository_contract import RepositoryContract

_TABLE = "screener-test"
_REGION = "us-east-1"


class TestDynamoDbRepository(RepositoryContract):
    @pytest.fixture
    def repo(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[ScreenerRepository]:
        # Dummy credentials so botocore's signer is satisfied while moto intercepts the calls.
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
            yield DynamoDbScreenerRepository(table)
