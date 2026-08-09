"""Anthropic implementation of ``SummaryProvider`` (F6, PRD FR-7). The optional AI stage: given an
assembled ``Dossier``, it asks Claude to **read the news articles behind the links** (via the
server-side web-fetch tool), judge whether anything is critical, and return one plain-English
"AI read". Off by default — only wired when an Anthropic key is configured.

Design (mirrors the other adapters): the network goes through an injectable ``create_message_fn``
seam so unit tests drive it with no network. Only the structured data already on the ``Dossier`` is
sent — plus the news URLs, which is what authorises web-fetch (it fetches only URLs already present
in the conversation, never arbitrary browsing). A server-tool turn can pause (``stop_reason ==
"pause_turn"``); we resume by echoing the assistant turn back, bounded so it can't loop forever. On
any API/transport failure this raises ``ProviderError`` — the F5 assembler catches it and degrades
to a footer note, so a failed summary never breaks the dossier.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from screener.domain.errors import ProviderError
from screener.domain.models import Dossier, FundamentalsSnapshot

# messages.create(**kwargs) -> Message-like object (.content: list of blocks, .stop_reason: str).
# Injected so tests need neither the network nor the anthropic SDK.
CreateMessageFn = Callable[..., Any]

_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 700
_MAX_CONTINUATIONS = 5  # bound the server-tool pause_turn resume loop

_SYSTEM = """You are an equity research assistant writing a short "AI read" of a stock dossier.

You are given structured fundamentals, a green/yellow/red scorecard, and a list of recent news
headlines each with a URL. Use the web-fetch tool to open and read the article behind each URL,
then write the read.

Rules:
- Ground every statement in the structured data provided or the article contents you fetched.
  Never introduce outside facts or invented numbers.
- For the news, judge what is genuinely material — regulatory/legal action, guidance changes,
  M&A, leadership changes, demand shifts — versus routine coverage. Call out anything critical;
  do not just restate headlines.
- This is descriptive analysis, not advice. Never give a buy/sell/hold recommendation or a price
  target of your own.

Output plain text only (no markdown), as these labelled lines, each one line, ~120-200 words total:
Strengths: <the strongest green/positive points>
Watch-outs: <the reds/yellows and the main risk>
News: <what the articles actually say, flagging anything critical vs. routine>
Near-term: <earnings timing and any imminent catalyst>
Net: <a soft synthesised read of how the greens, reds, and news balance out — a lean, not advice>
"""


class AnthropicSummaryProvider:
    def __init__(
        self,
        api_key: str,
        *,
        create_message_fn: CreateMessageFn | None = None,
        model: str = _MODEL,
        max_uses: int = 10,
        max_tokens: int = _MAX_TOKENS,
    ) -> None:
        self._create = create_message_fn or _default_create(api_key)
        self._model = model
        self._max_uses = max_uses
        self._max_tokens = max_tokens

    def summarize(self, dossier: Dossier) -> str:
        # web_fetch only retrieves URLs already in the conversation, so the news URLs in the prompt
        # are what it may open; max_uses bounds the fetch count (and therefore the cost).
        tools = [{"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": self._max_uses}]
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _render_dossier_facts(dossier)}
        ]
        try:
            for _ in range(_MAX_CONTINUATIONS + 1):
                response = self._create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=_SYSTEM,
                    tools=tools,
                    output_config={"effort": "medium"},
                    messages=messages,
                )
                if getattr(response, "stop_reason", None) != "pause_turn":
                    return _extract_text(response)
                # Server-tool loop paused mid-turn; resume by echoing the assistant turn back.
                messages.append({"role": "assistant", "content": response.content})
            raise ProviderError("Anthropic summary did not finish (pause_turn loop exhausted)")
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalise any SDK/transport error to the port signal
            raise ProviderError(f"Anthropic summary failed: {exc}") from exc


# ---------------------------------------------------------------------- internals
def _default_create(api_key: str) -> CreateMessageFn:
    """Lazily build a real Anthropic client (local import keeps the SDK off the free core path)."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def _create(**kwargs: Any) -> Any:
        return client.messages.create(**kwargs)

    return _create


def _extract_text(response: Any) -> str:
    parts = [
        block.text
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    if not text:
        raise ProviderError("Anthropic summary returned no text")
    return text


def _render_dossier_facts(dossier: Dossier) -> str:
    """The prompt payload: structured facts + the news list *with URLs*. Pure, so the exact bytes
    sent to the model are unit-testable without a network call."""
    p, s = dossier.profile, dossier.snapshot
    lines: list[str] = [
        f"Company: {p.symbol} — {p.name}",
        f"Sector / industry: {_join(p.sector, p.industry)}",
        "",
        f"Scorecard tally: {dossier.scorecard.tally or 'n/a'}",
    ]
    for line in dossier.scorecard.lines:
        value = f" [{line.value}]" if line.value else ""
        lines.append(f"- {line.category.value}: {line.flag.value}{value} — {line.note}")

    lines += ["", "Fundamentals (only known metrics shown):"]
    lines += [f"- {label}: {value}" for label, value in _metrics(s) if value is not None]

    lines += ["", "Recent news (fetch each URL to read the full article):"]
    if dossier.news:
        for item in dossier.news:
            lines.append(
                f"- {item.published_at:%Y-%m-%d} · {item.source} · {item.headline}\n  {item.url}"
            )
    else:
        lines.append("- (no recent news)")

    lines += ["", "Write the AI read now."]
    return "\n".join(lines)


def _metrics(s: FundamentalsSnapshot) -> list[tuple[str, str | None]]:
    """(label, rendered-value-or-None) — a ``None`` value drops the metric from the prompt."""
    return [
        ("P/E ttm", _num(s.pe_ttm)),
        ("P/E fwd", _num(s.pe_fwd)),
        ("P/S", _num(s.price_to_sales)),
        ("PEG", _num(s.peg)),
        ("EV/EBITDA", _num(s.ev_ebitda)),
        ("P/B", _num(s.price_to_book)),
        ("Revenue YoY", _pct(s.revenue_yoy)),
        ("EPS YoY", _pct(s.eps_yoy)),
        ("Revenue 3y CAGR", _pct(s.revenue_cagr_3y)),
        ("Gross margin", _pct(s.gross_margin)),
        ("Operating margin", _pct(s.operating_margin)),
        ("Net margin", _pct(s.net_margin)),
        ("ROE", _pct(s.roe)),
        ("FCF positive", _bool(s.fcf_positive)),
        ("Debt / equity", _num(s.debt_to_equity)),
        ("Current ratio", _num(s.current_ratio)),
        ("Net debt / EBITDA", _num(s.net_debt_to_ebitda)),
        ("Interest coverage", _num(s.interest_coverage)),
        ("Analyst rating", s.analyst_rating),
        ("Mean price target", _num(s.mean_target)),
        ("Last earnings surprise", _pct(s.last_earnings_surprise_pct)),
        ("Next earnings", s.next_earnings_date.isoformat() if s.next_earnings_date else None),
    ]


def _num(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def _pct(value: Decimal | None) -> str | None:
    return None if value is None else f"{value * 100:.1f}%"


def _bool(value: bool | None) -> str | None:
    return None if value is None else ("yes" if value else "no")


def _join(*parts: str | None) -> str:
    return " · ".join(x for x in parts if x) or "n/a"
