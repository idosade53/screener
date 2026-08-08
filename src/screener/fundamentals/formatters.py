"""Dossier text formatting (PRD FR-5, §4.1), styled after ``screener/formatters.py``. One shared
formatter for both surfaces — Telegram (chunked by the notifier) and CLI. Pure: it renders a
``Dossier`` and knows nothing of providers or transports. Missing metrics render ``n/a`` and never
break a section (PRD §4.1)."""

from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo

from screener.domain.models import (
    CompanyProfile,
    Dossier,
    Flag,
    FundamentalsSnapshot,
    NewsItem,
    Scorecard,
    ScoreCategory,
)

_ET = ZoneInfo("America/New_York")

_FLAG_GLYPH = {Flag.GREEN: "🟢", Flag.YELLOW: "🟡", Flag.RED: "🔴", Flag.NA: "⚪"}
_CATEGORY_LABEL = {
    ScoreCategory.VALUATION: "Valuation",
    ScoreCategory.GROWTH: "Growth",
    ScoreCategory.PROFITABILITY: "Profitability",
    ScoreCategory.BALANCE_SHEET: "Balance sheet",
    ScoreCategory.ANALYST: "Analyst view",
    ScoreCategory.EARNINGS_TIMING: "Earnings timing",
}


def format_dossier(dossier: Dossier) -> str:
    p, s = dossier.profile, dossier.snapshot
    lines: list[str] = []
    lines += _header(p)
    lines += _scorecard(dossier.scorecard)
    lines += _valuation(s)
    lines += _growth(s)
    lines += _profitability(s)
    lines += _balance_sheet(s)
    lines += _analyst(s)
    lines += _earnings(s)
    lines += _news(dossier.news)
    if dossier.ai_summary:
        lines += ["", "🤖 AI read", dossier.ai_summary]
    lines += _footer(dossier)
    return "\n".join(lines)


def _header(p: CompanyProfile) -> list[str]:
    sector = " · ".join(x for x in (p.sector, p.industry) if x) or "—"
    return [
        f"📄 {p.symbol} — {p.name}",
        sector,
        f"Market cap: {_money(p.market_cap)}",
    ]


def _scorecard(card: Scorecard) -> list[str]:
    out = ["", f"Scorecard: {card.tally or '—'}"]
    for line in card.lines:
        label = _CATEGORY_LABEL[line.category]
        value = f" — {line.value}" if line.value else ""
        out.append(f"{_FLAG_GLYPH[line.flag]} {label}{value}")
    return out


def _valuation(s: FundamentalsSnapshot) -> list[str]:
    return [
        "",
        "Valuation",
        f"  P/E ttm {_num(s.pe_ttm)} · fwd {_num(s.pe_fwd)} · PEG {_num(s.peg)}",
        f"  P/S {_num(s.price_to_sales)} · EV/EBITDA {_num(s.ev_ebitda)}"
        f" · P/B {_num(s.price_to_book)}",
    ]


def _growth(s: FundamentalsSnapshot) -> list[str]:
    return [
        "",
        "Growth",
        f"  Revenue YoY {_pct(s.revenue_yoy)} · EPS YoY {_pct(s.eps_yoy)}"
        f" · 3y CAGR {_pct(s.revenue_cagr_3y)}",
    ]


def _profitability(s: FundamentalsSnapshot) -> list[str]:
    return [
        "",
        "Profitability",
        f"  Gross {_pct(s.gross_margin)} · Oper {_pct(s.operating_margin)}"
        f" · Net {_pct(s.net_margin)}",
        f"  ROE {_pct(s.roe)} · FCF+ {_bool(s.fcf_positive)}",
    ]


def _balance_sheet(s: FundamentalsSnapshot) -> list[str]:
    return [
        "",
        "Balance sheet",
        f"  D/E {_num(s.debt_to_equity)} · Current {_num(s.current_ratio)}"
        f" · NetDebt/EBITDA {_num(s.net_debt_to_ebitda)} · Int cover {_num(s.interest_coverage)}",
    ]


def _analyst(s: FundamentalsSnapshot) -> list[str]:
    rating = s.analyst_rating or "n/a"
    count = f" ({s.num_analysts} analysts)" if s.num_analysts is not None else ""
    return [
        "",
        "Analyst view",
        f"  {rating}{count} · mean target {_num(s.mean_target)}",
    ]


def _earnings(s: FundamentalsSnapshot) -> list[str]:
    nxt = s.next_earnings_date.isoformat() if s.next_earnings_date else "n/a"
    return [
        "",
        "Earnings timing",
        f"  Next: {nxt} · last surprise {_pct(s.last_earnings_surprise_pct)}",
    ]


def _news(items: tuple[NewsItem, ...]) -> list[str]:
    out = ["", "News"]
    if not items:
        out.append("  (none)")
        return out
    for i in items:
        out.append(f"  {i.published_at:%Y-%m-%d} · {i.source} · {i.headline}")
        out.append(f"    {i.url}")
    return out


def _footer(dossier: Dossier) -> list[str]:
    et = dossier.generated_at.astimezone(_ET)
    out = [
        "",
        f"Sources: {dossier.snapshot.source} (fundamentals) · generated {et:%Y-%m-%d %H:%M} ET",
    ]
    for note in dossier.notes:
        out.append(f"⚠️ {note}")
    return out


# ---------------------------------------------------------------------- helpers
def _num(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _bool(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def _money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    v = float(value)
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= size:
            return f"${v / size:.2f}{unit}"
    return f"${v:.0f}"
