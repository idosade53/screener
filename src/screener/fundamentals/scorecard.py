"""The pure scorecard engine (PRD §4.2, FR-4). ``score`` turns a ``FundamentalsSnapshot`` into the
six-line ``Scorecard`` — no provider, telegram or DB knowledge, the same layering discipline as
``screener/criterion.py``. Every rule degrades on missing data to ``Flag.NA`` (never raises), so a
partial snapshot still produces a full card with the absent rows marked "no data" (PRD §4.1).

Thresholds are injected (``ScorecardThresholds``) rather than hard-coded (PRD §4.2). Analyst upside
needs the *current* price, which is not part of the snapshot (it is a scan/market figure, PRD §4.1
row 1), so ``price`` is passed alongside; when it is absent that one row is ``NA``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from screener.domain.models import (
    Flag,
    FundamentalsSnapshot,
    Scorecard,
    ScoreCategory,
    ScoreLine,
)
from screener.fundamentals.thresholds import ScorecardThresholds

_NO_DATA = "no data"


def score(
    snapshot: FundamentalsSnapshot,
    thresholds: ScorecardThresholds,
    *,
    today: date,
    price: Decimal | None = None,
) -> Scorecard:
    """Deterministic six-line scorecard. ``today`` anchors the earnings-timing row; ``price`` (the
    current market price, if known) anchors the analyst-upside row."""
    lines = (
        _score_valuation(snapshot, thresholds),
        _score_growth(snapshot, thresholds),
        _score_profitability(snapshot, thresholds),
        _score_balance_sheet(snapshot, thresholds),
        _score_analyst(snapshot, thresholds, price),
        _score_earnings_timing(snapshot, thresholds, today),
    )
    return Scorecard(lines=lines)


# --------------------------------------------------------------------------- valuation
def _score_valuation(s: FundamentalsSnapshot, t: ScorecardThresholds) -> ScoreLine:
    pe, peg = s.pe_ttm, s.peg
    if pe is None:
        return _na(ScoreCategory.VALUATION, _fmt_valuation(pe, peg))
    value = _fmt_valuation(pe, peg)
    if pe < 0:
        return ScoreLine(ScoreCategory.VALUATION, Flag.RED, value, "negative earnings")
    if pe > t.pe_red:
        return ScoreLine(ScoreCategory.VALUATION, Flag.RED, value, f"P/E above {t.pe_red}")
    # GREEN wants cheap *and* not over-paying for growth; a missing PEG can't veto an otherwise
    # cheap name, so it is treated permissively (down-weight, not fail — PRD §4.1).
    if pe < t.pe_green and (peg is None or peg < t.peg_green):
        return ScoreLine(ScoreCategory.VALUATION, Flag.GREEN, value, "cheap on earnings")
    return ScoreLine(ScoreCategory.VALUATION, Flag.YELLOW, value, "fairly valued")


# ----------------------------------------------------------------------------- growth
def _score_growth(s: FundamentalsSnapshot, t: ScorecardThresholds) -> ScoreLine:
    rev = s.revenue_yoy
    if rev is None:
        return _na(ScoreCategory.GROWTH, _fmt_growth(rev, s.eps_yoy))
    value = _fmt_growth(rev, s.eps_yoy)
    if rev < 0:
        return ScoreLine(ScoreCategory.GROWTH, Flag.RED, value, "revenue shrinking")
    if rev > t.rev_yoy_green:
        return ScoreLine(ScoreCategory.GROWTH, Flag.GREEN, value, "strong revenue growth")
    return ScoreLine(ScoreCategory.GROWTH, Flag.YELLOW, value, "modest growth")


# ------------------------------------------------------------------------ profitability
def _score_profitability(s: FundamentalsSnapshot, t: ScorecardThresholds) -> ScoreLine:
    nm = s.net_margin
    if nm is None:
        return _na(ScoreCategory.PROFITABILITY, _fmt_profitability(nm, s.fcf_positive))
    value = _fmt_profitability(nm, s.fcf_positive)
    if nm < 0:
        return ScoreLine(ScoreCategory.PROFITABILITY, Flag.RED, value, "unprofitable")
    if nm > t.net_margin_green and s.fcf_positive is True:
        return ScoreLine(ScoreCategory.PROFITABILITY, Flag.GREEN, value, "healthy margins, FCF+")
    return ScoreLine(ScoreCategory.PROFITABILITY, Flag.YELLOW, value, "thin but positive")


# ------------------------------------------------------------------------- balance sheet
def _score_balance_sheet(s: FundamentalsSnapshot, t: ScorecardThresholds) -> ScoreLine:
    de, cr = s.debt_to_equity, s.current_ratio
    if de is None and cr is None:
        return _na(ScoreCategory.BALANCE_SHEET, _fmt_balance_sheet(de, cr))
    value = _fmt_balance_sheet(de, cr)
    if (de is not None and de > t.de_red) or (cr is not None and cr < t.current_ratio_red):
        return ScoreLine(ScoreCategory.BALANCE_SHEET, Flag.RED, value, "stretched balance sheet")
    if de is not None and de < t.de_green and cr is not None and cr > t.current_ratio_green:
        return ScoreLine(ScoreCategory.BALANCE_SHEET, Flag.GREEN, value, "low leverage, liquid")
    return ScoreLine(ScoreCategory.BALANCE_SHEET, Flag.YELLOW, value, "moderate leverage")


# -------------------------------------------------------------------------- analyst view
def _score_analyst(
    s: FundamentalsSnapshot, t: ScorecardThresholds, price: Decimal | None
) -> ScoreLine:
    target, rating = s.mean_target, s.analyst_rating
    if target is None or price is None or price == 0:
        return _na(ScoreCategory.ANALYST, _fmt_analyst(target, rating, s.num_analysts, None))
    upside = (target - price) / price
    value = _fmt_analyst(target, rating, s.num_analysts, upside)
    if upside < 0:
        return ScoreLine(ScoreCategory.ANALYST, Flag.RED, value, "target below price")
    if upside > t.analyst_upside_green and _is_buy(rating):
        return ScoreLine(ScoreCategory.ANALYST, Flag.GREEN, value, "buy-rated with upside")
    return ScoreLine(ScoreCategory.ANALYST, Flag.YELLOW, value, "mixed analyst view")


# ----------------------------------------------------------------------- earnings timing
def _score_earnings_timing(
    s: FundamentalsSnapshot, t: ScorecardThresholds, today: date
) -> ScoreLine:
    nxt = s.next_earnings_date
    if nxt is None:
        return _na(ScoreCategory.EARNINGS_TIMING, None)
    days = _business_days_until(today, nxt)
    if days < 0:
        # The stored date is in the past — the snapshot is due a refresh; don't guess a verdict.
        return _na(ScoreCategory.EARNINGS_TIMING, f"{nxt.isoformat()} (passed)")
    value = f"in {days} trading day{'s' if days != 1 else ''} ({nxt.isoformat()})"
    if days > t.earnings_green_days:
        return ScoreLine(ScoreCategory.EARNINGS_TIMING, Flag.GREEN, value, "well clear of earnings")
    if days <= t.earnings_red_days:
        return ScoreLine(
            ScoreCategory.EARNINGS_TIMING, Flag.RED, value, "earnings imminent — caution"
        )
    return ScoreLine(ScoreCategory.EARNINGS_TIMING, Flag.YELLOW, value, "earnings approaching")


# ------------------------------------------------------------------------------- helpers
def _na(category: ScoreCategory, value: str | None) -> ScoreLine:
    return ScoreLine(category, Flag.NA, value, _NO_DATA)


def _is_buy(rating: str | None) -> bool:
    return rating is not None and "buy" in rating.lower()


def _business_days_until(today: date, target: date) -> int:
    """Weekday count in ``(today, target]`` — a holiday-agnostic approximation of "trading days
    out" (PRD §4.2; a calendar-accurate count is a §13 refinement). Negative if ``target`` is in
    the past."""
    if target <= today:
        return -1 if target < today else 0
    days = 0
    cur = today + timedelta(days=1)
    while cur <= target:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_valuation(pe: Decimal | None, peg: Decimal | None) -> str:
    return f"P/E {_num(pe)}, PEG {_num(peg)}"


def _fmt_growth(rev: Decimal | None, eps: Decimal | None) -> str:
    return f"rev YoY {_pct(rev)}, EPS YoY {_pct(eps)}"


def _fmt_profitability(nm: Decimal | None, fcf_positive: bool | None) -> str:
    fcf = "n/a" if fcf_positive is None else ("yes" if fcf_positive else "no")
    return f"net margin {_pct(nm)}, FCF+ {fcf}"


def _fmt_balance_sheet(de: Decimal | None, cr: Decimal | None) -> str:
    return f"D/E {_num(de)}, current ratio {_num(cr)}"


def _fmt_analyst(
    target: Decimal | None, rating: str | None, num: int | None, upside: Decimal | None
) -> str:
    parts = [f"target {_num(target)}"]
    if upside is not None:
        parts.append(f"({_pct(upside)})")
    if rating:
        parts.append(rating)
    if num is not None:
        parts.append(f"{num} analysts")
    return ", ".join(parts)
