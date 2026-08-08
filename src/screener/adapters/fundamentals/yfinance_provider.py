"""yfinance fallback for ``FundamentalsProvider`` (PRD §8, FR-2): zero-key, reusing the existing
yfinance dependency. Used when FMP fails or returns nothing. Everything is read off ``Ticker.info``
(plus ``.calendar`` for the next earnings date); the network seam is an injectable ``ticker_fn`` so
contract tests pass a recorded stand-in with no network — mirroring the market-data adapter.

yfinance quirks normalised here: ``debtToEquity`` arrives as a *percentage* (183.5 == 1.835×) so it
is divided by 100; margins/growth already arrive as fractions. Values are ``Decimal`` at 4 dp.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from screener.domain.errors import ProviderError
from screener.domain.models import CompanyProfile, FundamentalsSnapshot
from screener.indicators.quantize import to_decimal_4dp


class _TickerLike(Protocol):
    info: dict[str, Any]
    calendar: Any


def _default_ticker(symbol: str) -> _TickerLike:
    import yfinance as yf

    return cast(_TickerLike, yf.Ticker(symbol))


class YFinanceFundamentalsProvider:
    def __init__(self, *, ticker_fn: Callable[[str], _TickerLike] = _default_ticker) -> None:
        self._ticker_fn = ticker_fn

    def validate_symbol(self, symbol: str) -> bool:
        return bool(self._info(symbol))

    def fetch_profile(self, symbol: str) -> CompanyProfile:
        info = self._info(symbol)
        if not info:
            raise ProviderError(f"yfinance returned no info for {symbol}")
        return CompanyProfile(
            symbol=symbol,
            name=_str(info.get("longName") or info.get("shortName")) or symbol,
            sector=_str(info.get("sector")),
            industry=_str(info.get("industry")),
            market_cap=_dec(info.get("marketCap")),
            currency=_str(info.get("currency")),
            exchange=_str(info.get("exchange")),
        )

    def fetch_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        ticker = self._ticker_fn(symbol)
        info = _as_dict(getattr(ticker, "info", None))
        if not info:
            raise ProviderError(f"yfinance returned no info for {symbol}")
        return FundamentalsSnapshot(
            symbol=symbol,
            fetched_at=datetime.now().astimezone(),
            source="yfinance",
            next_earnings_date=_next_earnings(getattr(ticker, "calendar", None)),
            pe_ttm=_dec(info.get("trailingPE")),
            pe_fwd=_dec(info.get("forwardPE")),
            price_to_sales=_dec(info.get("priceToSalesTrailing12Months")),
            peg=_dec(info.get("trailingPegRatio") or info.get("pegRatio")),
            ev_ebitda=_dec(info.get("enterpriseToEbitda")),
            price_to_book=_dec(info.get("priceToBook")),
            revenue_yoy=_dec(info.get("revenueGrowth")),
            eps_yoy=_dec(info.get("earningsGrowth")),
            revenue_cagr_3y=None,
            gross_margin=_dec(info.get("grossMargins")),
            operating_margin=_dec(info.get("operatingMargins")),
            net_margin=_dec(info.get("profitMargins")),
            roe=_dec(info.get("returnOnEquity")),
            fcf_positive=_positive(info.get("freeCashflow")),
            debt_to_equity=_ratio_from_pct(info.get("debtToEquity")),
            current_ratio=_dec(info.get("currentRatio")),
            net_debt_to_ebitda=None,
            interest_coverage=None,
            analyst_rating=_str(info.get("recommendationKey")),
            num_analysts=_int(info.get("numberOfAnalystOpinions")),
            mean_target=_dec(info.get("targetMeanPrice")),
            last_earnings_surprise_pct=None,
        )

    def _info(self, symbol: str) -> dict[str, Any]:
        return _as_dict(getattr(self._ticker_fn(symbol), "info", None))


# ---------------------------------------------------------------------- helpers
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _next_earnings(calendar: Any) -> date | None:
    """``Ticker.calendar`` is a dict like ``{"Earnings Date": [date, ...]}`` (older yfinance
    returned a DataFrame). Pick the soonest date; tolerate either shape and missing keys."""
    dates: list[date] = []
    if isinstance(calendar, dict):
        raw = calendar.get("Earnings Date") or calendar.get("earningsDate") or []
        raw = raw if isinstance(raw, list) else [raw]
        for item in raw:
            d = _coerce_date(item)
            if d is not None:
                dates.append(d)
    return min(dates) if dates else None


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return to_decimal_4dp(float(value))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _ratio_from_pct(value: Any) -> Decimal | None:
    d = _dec(value)
    return None if d is None else to_decimal_4dp(float(d / 100))


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
