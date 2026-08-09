"""F6 AnthropicSummaryProvider — no network. The injected ``create_message_fn`` stands in for the
SDK: we assert the prompt payload carries the structured facts + news URLs (and omits missing
metrics), that a single call returns its text, that a ``pause_turn`` turn is resumed exactly once,
and that any SDK/transport error is normalised to ``ProviderError`` (the port's failure signal)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from screener.adapters.summary.anthropic_provider import (
    AnthropicSummaryProvider,
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
        summary=None,
    )


# ------------------------------------------------------------------------- tests
def test_render_includes_facts_and_news_urls_and_omits_missing_metrics() -> None:
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
    # News headline *and* URL are handed over so web-fetch can open the article.
    assert "Apple unveils new chip" in rendered
    assert "https://example.com/apple-chip" in rendered


def test_no_news_renders_placeholder() -> None:
    assert "(no recent news)" in _render_dossier_facts(_dossier())


def test_summarize_returns_text_in_one_call() -> None:
    fake = _FakeCreate(_Message(content=[_Block("text", "Strengths: strong margins.\nNet: fine.")]))
    provider = AnthropicSummaryProvider("key", create_message_fn=fake)

    out = provider.summarize(_dossier(news=(_news_item(),)))

    assert out == "Strengths: strong margins.\nNet: fine."
    assert len(fake.calls) == 1
    # The web-fetch tool is offered so Claude can read the article behind the link.
    assert fake.calls[0]["tools"][0]["name"] == "web_fetch"


def test_pause_turn_is_resumed_once_then_completes() -> None:
    paused = _Message(content=[_Block("server_tool_use", "")], stop_reason="pause_turn")
    done = _Message(content=[_Block("text", "News: nothing critical.")])
    fake = _FakeCreate(paused, done)
    provider = AnthropicSummaryProvider("key", create_message_fn=fake)

    out = provider.summarize(_dossier(news=(_news_item(),)))

    assert out == "News: nothing critical."
    assert len(fake.calls) == 2
    # The resume echoes the paused assistant turn back so the server tool loop continues.
    assert fake.calls[1]["messages"][-1]["role"] == "assistant"
    assert fake.calls[1]["messages"][-1]["content"] == paused.content


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
