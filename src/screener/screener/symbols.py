"""Shared symbol normalisation (M5T04). Pure core logic reused by the CLI
(``composition/cli.py``) and the Telegram bot (``bot/commands.py``).

The bot cannot print skipped symbols to stderr the way the CLI does, so this returns a
structured result — valid (deduped, order-preserved) and invalid — leaving the caller to
render feedback in whatever channel it owns. Regex per architecture §8.6."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

SYMBOL_RE = r"^[A-Z][A-Z0-9.\-]{0,9}$"
_PATTERN = re.compile(SYMBOL_RE)


@dataclass(frozen=True)
class NormaliseResult:
    valid: list[str]
    invalid: list[str]


def normalise(symbols: Sequence[str]) -> NormaliseResult:
    """Strip/upper-case each symbol, drop ones failing ``SYMBOL_RE`` (reported as invalid),
    and dedupe the survivors while preserving first-seen order."""
    valid: list[str] = []
    invalid: list[str] = []
    for s in symbols:
        u = s.strip().upper()
        if not _PATTERN.match(u):
            invalid.append(s)
            continue
        if u not in valid:
            valid.append(u)
    return NormaliseResult(valid=valid, invalid=invalid)
