from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from screener.adapters.repository.sqlite_repository import SqliteScreenerRepository
from screener.ports.repository import ScreenerRepository
from tests.contract.repository_contract import RepositoryContract


class TestSqliteRepository(RepositoryContract):
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Iterator[ScreenerRepository]:
        db = SqliteScreenerRepository(str(tmp_path / "screener.db"))
        try:
            yield db
        finally:
            db.close()
