"""Financial Modeling Prep implementation of ``FundamentalsProvider`` (PRD §8, FR-2). FMP is the
primary fundamentals feed; like the yfinance market-data adapter it is the most likely component to
be swapped, so every FMP-shaped detail is normalised here and nothing leaks past this edge.

Resilience contract (mirrors ``market_data``): network goes through an injectable ``http_get_fn``
seam so contract tests replay recorded JSON with no network; each call is retried (3×, exponential
backoff) internally. A *partial* outage (one endpoint down) degrades to ``None`` for that section;
a *total* outage — no company profile at all — raises ``ProviderError`` so the assembler (F5) can
fall back to yfinance. Money/ratio values are ``Decimal`` quantised to 4 dp at the boundary.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from screener.domain.errors import ProviderError
from screener.domain.models import CompanyProfile, FundamentalsSnapshot
from screener.indicators.quantize import to_decimal_4dp

# (url, params, timeout_seconds) -> parsed JSON body (list or dict). Injected for testability.
HttpGetFn = Callable[[str, dict[str, str], float], Any]

_BASE = "https://financialmodelingprep.com/api/v3"


def _default_get(url: str, params: dict[str, str], timeout: float) -> Any:
    import httpx

    return httpx.get(url, params=params, timeout=timeout).json()


class FmpFundamentalsProvider:
    def __init__(
        self,
        api_key: str,
        *,
        http_get_fn: HttpGetFn | None = None,
        today_fn: Callable[[], date] = date.today,
        timeout: float = 10.0,
        retries: int = 3,
        backoff_base: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = api_key
        self._get = http_get_fn or _default_get
        self._today = today_fn
        self._timeout = timeout
        self._retries = retries
        self._backoff_base = backoff_base
        self._sleep = sleep_fn

    # ----------------------------------------------------------------- public API
    def validate_symbol(self, symbol: str) -> bool:
        return self._first(self._fetch(f"profile/{symbol}")) is not None

    def fetch_profile(self, symbol: str) -> CompanyProfile:
        profile = self._first(self._fetch(f"profile/{symbol}"))
        if profile is None:
            raise ProviderError(f"FMP returned no profile for {symbol}")
        return _to_profile(symbol, profile)

    def fetch_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        # The profile anchors the request; without it we have nothing and signal a fallback.
        profile = self._first(self._fetch(f"profile/{symbol}"))
        if profile is None:
            raise ProviderError(f"FMP returned no profile for {symbol}")

        # Every other section degrades independently to {} on its own outage (partial dossier).
        ratios = self._first(self._fetch(f"ratios-ttm/{symbol}")) or {}
        metrics = self._first(self._fetch(f"key-metrics-ttm/{symbol}")) or {}
        income = self._fetch(f"income-statement/{symbol}", {"period": "annual", "limit": "2"}) or []
        target = self._first(self._fetch(f"price-target-consensus/{symbol}")) or {}
        earnings = self._fetch(f"historical/earning_calendar/{symbol}") or []

        rev_yoy, eps_yoy = _yoy(income if isinstance(income, list) else [])
        return FundamentalsSnapshot(
            symbol=symbol,
            fetched_at=_now(),
            source="fmp",
            next_earnings_date=self._next_earnings(earnings if isinstance(earnings, list) else []),
            pe_ttm=_dec(ratios.get("peRatioTTM")),
            pe_fwd=_dec(metrics.get("forwardPETTM")),
            price_to_sales=_dec(ratios.get("priceToSalesRatioTTM")),
            peg=_dec(ratios.get("priceEarningsToGrowthRatioTTM")),
            ev_ebitda=_dec(metrics.get("enterpriseValueOverEBITDATTM")),
            price_to_book=_dec(ratios.get("priceToBookRatioTTM")),
            revenue_yoy=rev_yoy,
            eps_yoy=eps_yoy,
            revenue_cagr_3y=None,  # needs 4 annual periods; not fetched on the on-demand path
            gross_margin=_dec(ratios.get("grossProfitMarginTTM")),
            operating_margin=_dec(ratios.get("operatingProfitMarginTTM")),
            net_margin=_dec(ratios.get("netProfitMarginTTM")),
            roe=_dec(ratios.get("returnOnEquityTTM")),
            fcf_positive=_positive(metrics.get("freeCashFlowPerShareTTM")),
            debt_to_equity=_dec(ratios.get("debtEquityRatioTTM")),
            current_ratio=_dec(ratios.get("currentRatioTTM")),
            net_debt_to_ebitda=_dec(metrics.get("netDebtToEBITDATTM")),
            interest_coverage=_dec(ratios.get("interestCoverageTTM")),
            analyst_rating=_str(target.get("consensus") or profile.get("rating")),
            num_analysts=_int(target.get("numberOfAnalysts")),
            mean_target=_dec(target.get("targetConsensus")),
            last_earnings_surprise_pct=None,
        )

    # ------------------------------------------------------------------ internals
    def _fetch(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = {"apikey": self._key, **(params or {})}
        url = f"{_BASE}/{path}"
        for attempt in range(self._retries):
            try:
                result = self._get(url, query, self._timeout)
            except Exception:  # noqa: BLE001 — retry transient provider failures internally
                result = None
            if result:  # non-empty list/dict is a hit
                return result
            if attempt < self._retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
        return None

    @staticmethod
    def _first(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict) and payload:
            return payload
        return None

    def _next_earnings(self, rows: list[Any]) -> date | None:
        today = self._today()
        dates = sorted(
            d for d in (_parse_date(r.get("date")) for r in rows if isinstance(r, dict)) if d
        )
        if not dates:
            return None
        upcoming = [d for d in dates if d >= today]
        return upcoming[0] if upcoming else dates[-1]


# ---------------------------------------------------------------------- helpers
def _now() -> Any:
    from datetime import datetime

    return datetime.now().astimezone()


def _to_profile(symbol: str, p: dict[str, Any]) -> CompanyProfile:
    return CompanyProfile(
        symbol=symbol,
        name=_str(p.get("companyName")) or symbol,
        sector=_str(p.get("sector")),
        industry=_str(p.get("industry")),
        market_cap=_dec(p.get("mktCap")),
        currency=_str(p.get("currency")),
        exchange=_str(p.get("exchangeShortName")),
    )


def _yoy(income: list[Any]) -> tuple[Decimal | None, Decimal | None]:
    """Revenue/EPS year-over-year from the two most recent annual statements (index 0 newest)."""
    if len(income) < 2 or not isinstance(income[0], dict) or not isinstance(income[1], dict):
        return None, None
    return (
        _growth(income[0].get("revenue"), income[1].get("revenue")),
        _growth(income[0].get("eps"), income[1].get("eps")),
    )


def _growth(cur: Any, prev: Any) -> Decimal | None:
    c, p = _dec(cur), _dec(prev)
    if c is None or p is None or p == 0:
        return None
    return to_decimal_4dp(float((c - p) / abs(p)))


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return to_decimal_4dp(float(value))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _positive(value: Any) -> bool | None:
    d = _dec(value)
    return None if d is None else d > 0


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
