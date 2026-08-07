from __future__ import annotations

from screener.domain.models import ScanType
from screener.screener.diff import compute_diff


def test_no_change_does_not_send() -> None:
    diff = compute_diff(
        current_in_range=["AAPL", "KO"],
        previous_in_range=["KO", "AAPL"],
        scan_type=ScanType.OPEN,
        is_first_of_day=False,
    )
    assert diff.should_send is False
    assert diff.changed is False


def test_change_sends_with_entries_and_exits() -> None:
    diff = compute_diff(
        current_in_range=["AAPL", "NVDA"],
        previous_in_range=["AAPL", "MSFT"],
        scan_type=ScanType.OPEN,
        is_first_of_day=False,
    )
    assert diff.should_send is True
    assert diff.entered == frozenset({"NVDA"})
    assert diff.exited == frozenset({"MSFT"})


def test_first_of_day_always_sends() -> None:
    diff = compute_diff(
        current_in_range=["AAPL"],
        previous_in_range=["AAPL"],
        scan_type=ScanType.PRE,
        is_first_of_day=True,
    )
    assert diff.should_send is True


def test_manual_always_sends() -> None:
    diff = compute_diff(
        current_in_range=["AAPL"],
        previous_in_range=["AAPL"],
        scan_type=ScanType.MANUAL,
        is_first_of_day=False,
    )
    assert diff.should_send is True
