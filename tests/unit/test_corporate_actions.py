from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from screener.domain.models import Bar
from screener.screener.corporate_actions import detect_split


def _bars(closes: list[str], start: date = date(2026, 8, 1)) -> list[Bar]:
    out = []
    for i, c in enumerate(closes):
        d = start + timedelta(days=i)
        out.append(Bar(date=d, open=Decimal(c), high=Decimal(c), low=Decimal(c),
                       close=Decimal(c), volume=1000))
    return out


def test_detects_2_for_1_split() -> None:
    stored = _bars(["100", "102", "104"])
    fetched = _bars(["50", "51", "52"])  # every prior bar halved
    assert detect_split(stored, fetched) is True


def test_detects_reverse_split() -> None:
    stored = _bars(["10", "11", "12"])
    fetched = _bars(["100", "110", "120"])  # 1:10 reverse split
    assert detect_split(stored, fetched) is True


def test_large_gap_down_is_not_a_split() -> None:
    # A one-day 30% gap-down: the overlapping prior bars are unchanged, only the newest moves.
    stored = _bars(["100", "101", "102"])
    fetched = _bars(["100", "101", "71"])
    assert detect_split(stored, fetched) is False


def test_no_overlap_is_not_a_split() -> None:
    stored = _bars(["100"], start=date(2026, 8, 1))
    fetched = _bars(["50"], start=date(2026, 9, 1))
    assert detect_split(stored, fetched) is False


def test_identical_history_is_not_a_split() -> None:
    stored = _bars(["100", "101", "102"])
    assert detect_split(stored, stored) is False
