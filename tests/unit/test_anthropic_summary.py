"""AnthropicSummaryProvider — no network (F6, reworked in F7T01). The injected ``create_message_fn``
stands in for the SDK: we assert the prompt payload carries the structured facts + each news item's
summary (and omits missing metrics), that a single call with no web-fetch tool returns its text,
that ``_extract_text`` returns only the final read, and that any SDK/transport error is normalised
to ``ProviderError`` (the port's failure signal)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from screener.adapters.summary.anthropic_provider import (
    AnthropicSummaryProvider,
    _extract_text,
    _render_dossier_facts,
)
from screener.domain.errors import ProviderError
from screener.domain.models import (
    CompanyProfile,
    Dossier,
    Flag,
    FundamentalsSnapshot,
    NewsItem,
    Scorecard,
    ScoreCategory,
    ScoreLine,
)

_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ fake SDK objects
@dataclass
class _Block:
    type: str
    text: str = ""


@dataclass
class _Message:
    content: list[Any]
    stop_reason: str = "end_turn"


class _FakeCreate:
    """Records calls and returns a scripted sequence of responses (one per call)."""

    def __init__(self, *responses: _Message) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> _Message:
        self.calls.append(kwargs)
        return self._responses[len(self.calls) - 1]


# ---------------------------------------------------------------------- fixtures
def _dossier(*, news: tuple[NewsItem, ...] = ()) -> Dossier:
    profile = CompanyProfile(
        symbol="AAPL",
        name="Apple Inc.",
        sector="Technology",
        industry="Consumer Electronics",
        market_cap=Decimal("3200000000000"),
        currency="USD",
        exchange="NASDAQ",
    )
    snapshot = FundamentalsSnapshot(
        symbol="AAPL",
        fetched_at=_NOW,
        source="fmp",
        next_earnings_date=None,
        pe_ttm=Decimal("28.5"),
        pe_fwd=None,  # missing -> must be omitted from the prompt
        price_to_sales=None,
        peg=Decimal("1.10"),
        ev_ebitda=None,
        price_to_book=None,
        revenue_yoy=Decimal("0.08"),
        eps_yoy=None,
        revenue_cagr_3y=None,
        gross_margin=Decimal("0.44"),
        operating_margin=None,
        net_margin=None,
        roe=None,
        fcf_positive=True,
        debt_to_equity=None,
        current_ratio=None,
        net_debt_to_ebitda=None,
        interest_coverage=None,
        analyst_rating="Buy",
        num_analysts=30,
        mean_target=None,
        last_earnings_surprise_pct=None,
    )
    scorecard = Scorecard(
        lines=(
            ScoreLine(
                category=ScoreCategory.VALUATION,
                flag=Flag.YELLOW,
                value="P/E 28.5, PEG 1.10",
                note="Rich but not extreme",
            ),
            ScoreLine(
                category=ScoreCategory.PROFITABILITY,
                flag=Flag.GREEN,
                value="Gross 44%",
                note="Healthy margins",
            ),
        )
    )
    return Dossier(
        symbol="AAPL",
        profile=profile,
        snapshot=snapshot,
        scorecard=scorecard,
        news=news,
        generated_at=_NOW,
    )


def _news_item() -> NewsItem:
    return NewsItem(
        published_at=datetime(2026, 8, 9, 14, tzinfo=UTC),
        source="Reuters",
        headline="Apple unveils new chip",
        url="https://example.com/apple-chip",
        summary="Apple announced its next-generation M-series chip. (sentiment: Bullish)",
    )


# ------------------------------------------------------------------------- tests
def test_render_includes_facts_and_news_summaries_and_omits_missing_metrics() -> None:
    rendered = _render_dossier_facts(_dossier(news=(_news_item(),)))

    # Scorecard flags + the driving values.
    assert "VALUATION: YELLOW [P/E 28.5, PEG 1.10]" in rendered
    assert "PROFITABILITY: GREEN [Gross 44%]" in rendered
    # Known metrics present, rendered from the snapshot.
    assert "P/E ttm: 28.50" in rendered
    assert "Gross margin: 44.0%" in rendered
    # Missing metrics are dropped, not rendered as n/a.
    assert "P/E fwd" not in rendered
    assert "Net margin" not in rendered
    # News headline *and* its summary text are handed over (F7T01 — the substance, not just a URL).
    assert "Apple unveils new chip" in rendered
    assert "next-generation M-series chip" in rendered
    assert "(sentiment: Bullish)" in rendered


def test_no_news_renders_placeholder() -> None:
    assert "(no recent news)" in _render_dossier_facts(_dossier())


def test_summarize_returns_text_in_one_call_without_web_fetch() -> None:
    fake = _FakeCreate(_Message(content=[_Block("text", "Strengths: strong margins.\nNet: fine.")]))
    provider = AnthropicSummaryProvider("key", create_message_fn=fake)

    out = provider.summarize(_dossier(news=(_news_item(),)))

    assert out == "Strengths: strong margins.\nNet: fine."
    assert len(fake.calls) == 1
    # F7T01: web-fetch is gone — no tools offered, and a larger token budget for the read.
    assert "tools" not in fake.calls[0]
    assert fake.calls[0]["max_tokens"] == 1500


def test_extract_text_returns_only_the_final_read() -> None:
    # A response with leading narration then the read must yield only the last text block.
    response = _Message(
        content=[
            _Block("text", "Let me look at the news...  "),
            _Block("text", "Strengths: solid.\nNet: balanced."),
        ]
    )
    assert _extract_text(response) == "Strengths: solid.\nNet: balanced."


def test_api_error_becomes_provider_error() -> None:
    def _boom(**_: Any) -> Any:
        raise RuntimeError("network down")

    provider = AnthropicSummaryProvider("key", create_message_fn=_boom)
    with pytest.raises(ProviderError):
        provider.summarize(_dossier())


def test_empty_response_becomes_provider_error() -> None:
    fake = _FakeCreate(_Message(content=[_Block("server_tool_use", "")]))  # no text block
    provider = AnthropicSummaryProvider("key", create_message_fn=fake)
    with pytest.raises(ProviderError):
        provider.summarize(_dossier())
