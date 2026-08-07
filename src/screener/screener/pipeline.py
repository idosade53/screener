"""The scan pipeline (architecture §6). Ten stages honouring three load-bearing orderings:

1. Persist before notify (8 before 10): a crash costs one missed alert, never a hole in history.
2. Claim before work (2 before 4): a retried invocation must not re-run a completed scan.
3. Capture ``previous`` before ``save_scan`` commits (9 after 8, but the previous summary is read
   at stage 1): the diff must compare against the *prior* scan, not the one being written.

Symbol-scoped failures never escalate to scan-scoped; scan-scoped failures never degrade into a
plausible-looking alert (architecture §8.3).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from screener.config import Settings
from screener.domain.errors import StaleDataError
from screener.domain.models import (
    DeliveryStatus,
    Indicators,
    PriceMode,
    ScanContext,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    SymbolStatus,
)
from screener.indicators.compute import latest_indicators
from screener.ports.calendar import TradingCalendar
from screener.ports.clock import Clock
from screener.ports.market_data import MarketDataProvider
from screener.ports.notifier import Notifier
from screener.ports.repository import ScreenerRepository
from screener.screener.context import resolve_context
from screener.screener.corporate_actions import detect_split
from screener.screener.criterion import Criterion
from screener.screener.diff import compute_diff
from screener.screener.formatters import format_scan_message

log = logging.getLogger("screener.pipeline")

_SYSTEM_PREFIX = "⚠️ SYSTEM"
_ET = ZoneInfo("America/New_York")


class ScanPipeline:
    def __init__(
        self,
        *,
        repo: ScreenerRepository,
        provider: MarketDataProvider,
        calendar: TradingCalendar,
        clock: Clock,
        notifier: Notifier,
        criteria: Sequence[Criterion],
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._calendar = calendar
        self._clock = clock
        self._notifier = notifier
        self._criteria = list(criteria)
        self._cfg = settings

    def run(self, scan_type: ScanType) -> ScanSummary:
        now = self._clock.now()
        today = now.astimezone(_ET).date()

        # ── Stage 1: RESOLVE CONTEXT ──────────────────────────────────────────
        if scan_type in (ScanType.PRE, ScanType.OPEN, ScanType.CLOSE) and not (
            self._calendar.is_trading_day(today)
        ):
            log.info("skip: %s is not a trading day", today)
            return self._skipped(scan_type, now, today, "not a trading day")

        trading_day = (
            today
            if self._calendar.is_trading_day(today)
            else self._calendar.previous_trading_day(today)
        )
        is_first_of_day = len(self._repo.scans_on(trading_day)) == 0
        context = resolve_context(
            scan_type=scan_type,
            now=now,
            calendar=self._calendar,
            is_first_of_day=is_first_of_day,
            scheduled_times=self._cfg.scheduled_times,
        )
        # Capture the diff baseline BEFORE any write (ordering invariant 3).
        previous_summary = self._repo.latest_scan()

        # ── Stage 2: CLAIM ────────────────────────────────────────────────────
        if not self._repo.try_claim_scan(context.scan_id):
            log.info("skip: scan %s already claimed", context.scan_id)
            return self._skipped(scan_type, now, trading_day, "already claimed")

        # A missed earlier scan of the day surfaces here, on the first scan to run after the gap.
        self._check_missed_scans(context)

        # ── Stage 3: LOAD UNIVERSE ────────────────────────────────────────────
        universe = [m.symbol for m in self._repo.get_universe()]
        if not universe:
            self._notify_system("Universe is empty — nothing to scan.")
            return self._skipped(scan_type, now, trading_day, "empty universe")

        # ── Stage 4: REFRESH BARS (CLOSE / MANUAL) ───────────────────────────
        data_error: dict[str, str] = {}
        if scan_type in (ScanType.CLOSE, ScanType.MANUAL):
            data_error.update(self._refresh_bars(universe, context))
            try:
                self._staleness_guard(universe, context)
            except StaleDataError as exc:
                self._notify_system(f"stale data — {exc}")
                return self._aborted(context, universe, str(exc))

        # ── Stage 5: INDICATORS ───────────────────────────────────────────────
        indicators, insufficient = self._resolve_indicators(universe, context)

        # ── Stage 6: PRICES ───────────────────────────────────────────────────
        prices, stale = self._resolve_prices(universe, context, indicators, data_error)

        # ── Stage 7: EVALUATE ─────────────────────────────────────────────────
        results = self._evaluate(
            universe, indicators, insufficient, data_error, prices, stale
        )

        # ── Stage 8: PERSIST (before notify) ─────────────────────────────────
        summary = self._build_summary(context, results, ScanStatus.OK)
        self._repo.save_scan(summary, results)

        # ── Stage 9: DIFF ─────────────────────────────────────────────────────
        diff = compute_diff(
            current_in_range=[r.symbol for r in results if r.in_range],
            previous_in_range=previous_summary.in_range if previous_summary else [],
            scan_type=scan_type,
            is_first_of_day=is_first_of_day,
        )

        # ── Stage 10: NOTIFY (failure here is logged, never raised) ───────────
        if diff.should_send:
            message = format_scan_message(
                context=context, summary=summary, results=results, diff=diff
            )
            status = self._send(message)
            self._repo.record_alert(context.scan_id, message, status)

        log.info(
            "scan %s done: %d in range, %d scanned",
            context.scan_id,
            len(summary.in_range),
            summary.symbols_scanned,
        )
        return summary

    # ------------------------------------------------------------------ stage 4
    def _refresh_bars(self, universe: list[str], context: ScanContext) -> dict[str, str]:
        end = context.indicator_asof
        backfill_start = end - timedelta(days=365 * self._cfg.backfill_years + 10)
        latest = {s: self._repo.latest_bar_date(s) for s in universe}

        if any(v is None for v in latest.values()):
            start = backfill_start  # a new symbol needs full history
        else:
            earliest = min(d for d in latest.values() if d is not None)
            start = min(earliest - timedelta(days=7), end)  # 7-day overlap for split detection

        result = self._provider.fetch_daily_bars(universe, start, end)
        failures = dict(result.failures)

        for sym, bars in result.bars.items():
            stored = self._repo.get_bars(sym, since=start)
            if stored and detect_split(stored, bars):
                log.warning("split detected for %s — refetching full history", sym)
                self._repo.delete_bars(sym)
                full = self._provider.fetch_daily_bars([sym], backfill_start, end)
                if sym in full.bars:
                    self._repo.upsert_bars(sym, full.bars[sym])
                # One missed observation is cheaper than one wrong one (§7.3).
                failures[sym] = "split refetch — skipped this scan"
                continue
            self._repo.upsert_bars(sym, bars)
        return failures

    def _staleness_guard(self, universe: list[str], context: ScanContext) -> None:
        expected = context.indicator_asof
        fresh = sum(
            1
            for s in universe
            if (d := self._repo.latest_bar_date(s)) is not None and d >= expected
        )
        min_fresh = (1.0 - self._cfg.stale_failure_fraction) * len(universe)
        if fresh < min_fresh:
            raise StaleDataError(
                f"only {fresh}/{len(universe)} symbols current through {expected}"
            )

    # ------------------------------------------------------------------ stage 5
    def _resolve_indicators(
        self, universe: list[str], context: ScanContext
    ) -> tuple[dict[str, Indicators], set[str]]:
        indicators: dict[str, Indicators] = {}
        insufficient: set[str] = set()

        if context.scan_type in (ScanType.PRE, ScanType.OPEN):
            cached = self._repo.get_indicators(universe, asof=context.indicator_asof)
            indicators.update(cached)
            missing = [s for s in universe if s not in cached]
            if missing:
                log.info("indicator cache miss for %d symbols; recomputing", len(missing))
        else:
            missing = list(universe)

        for sym in missing:
            bars = self._repo.get_bars(
                sym,
                since=context.indicator_asof
                - timedelta(days=365 * self._cfg.backfill_years + 10),
            )
            ind = latest_indicators(
                bars, self._cfg.sma_period, self._cfg.atr_period, self._cfg.min_bars_required
            )
            if ind is None:
                insufficient.add(sym)
            elif ind.asof == context.indicator_asof:
                indicators[sym] = ind
            else:
                # Bars aren't current through the expected session; treat as insufficient for
                # this scan rather than evaluating a stale band.
                insufficient.add(sym)

        # Cache freshly computed values at CLOSE so PRE/OPEN read them tomorrow (§7.4).
        if context.scan_type is ScanType.CLOSE and indicators:
            self._repo.put_indicators(indicators)
        return indicators, insufficient

    # ------------------------------------------------------------------ stage 6
    def _resolve_prices(
        self,
        universe: list[str],
        context: ScanContext,
        indicators: dict[str, Indicators],
        data_error: dict[str, str],
    ) -> tuple[dict[str, Decimal], set[str]]:
        """Returns price-by-symbol plus the set that fell back to a stale previous close."""
        prices: dict[str, Decimal] = {}
        stale: set[str] = set()

        if context.price_mode is PriceMode.OFFICIAL_CLOSE:
            # P is the official close = the close of the indicator_asof bar (§4.3).
            for sym in universe:
                bars = self._repo.get_bars(sym, since=context.indicator_asof)
                todays = [b for b in bars if b.date == context.indicator_asof]
                if todays:
                    prices[sym] = todays[-1].close
                elif sym not in data_error:
                    data_error.setdefault(sym, "no official close bar")
            return prices, stale

        quote_result = self._provider.fetch_quotes(universe, context.price_mode)
        for sym in universe:
            quote = quote_result.quotes.get(sym)
            if quote is not None:
                prices[sym] = quote.price
                continue
            # No live quote: fall back to the previous close and mark stale (Q2).
            prev = self._previous_close(sym, context.indicator_asof)
            if prev is not None:
                prices[sym] = prev
                stale.add(sym)
            elif sym not in data_error:
                data_error.setdefault(sym, quote_result.failures.get(sym, "no quote"))
        return prices, stale

    def _previous_close(self, symbol: str, asof: date) -> Decimal | None:
        bars = self._repo.get_bars(symbol, since=asof - timedelta(days=10))
        eligible = [b for b in bars if b.date <= asof]
        return eligible[-1].close if eligible else None

    # ------------------------------------------------------------------ stage 7
    def _evaluate(
        self,
        universe: list[str],
        indicators: dict[str, Indicators],
        insufficient: set[str],
        data_error: dict[str, str],
        prices: dict[str, Decimal],
        stale: set[str],
    ) -> list[SymbolScanResult]:
        results: list[SymbolScanResult] = []
        for sym in universe:
            if sym in data_error:
                results.append(_result(sym, SymbolStatus.DATA_ERROR))
                continue
            if sym in insufficient:
                results.append(_result(sym, SymbolStatus.INSUFFICIENT_DATA))
                continue
            ind = indicators.get(sym)
            price = prices.get(sym)
            if ind is None or price is None:
                results.append(_result(sym, SymbolStatus.DATA_ERROR))
                continue

            passed: list[bool] = []
            distance = None
            for crit in self._criteria:
                res = crit.evaluate(ind, price)
                if distance is None:
                    distance = res.distance_atr
                passed.append(res.passed)
            combined = all(passed) if self._cfg.alert_combinator == "ALL" else any(passed)

            status = SymbolStatus.STALE_PRICE if sym in stale else SymbolStatus.OK
            results.append(
                SymbolScanResult(
                    symbol=sym,
                    status=status,
                    price=price,
                    indicators=ind,
                    distance_atr=distance,
                    in_range=combined,
                )
            )
        return results

    # --------------------------------------------------------------- summaries
    def _build_summary(
        self,
        context: ScanContext,
        results: Sequence[SymbolScanResult],
        status: ScanStatus,
    ) -> ScanSummary:
        in_range = tuple(sorted(r.symbol for r in results if r.in_range))
        errors = tuple(sorted(r.symbol for r in results if r.status is SymbolStatus.DATA_ERROR))
        insuff = tuple(
            sorted(r.symbol for r in results if r.status is SymbolStatus.INSUFFICIENT_DATA)
        )
        return ScanSummary(
            scan_id=context.scan_id,
            scan_type=context.scan_type,
            scheduled_at=context.ran_at,
            ran_at=context.ran_at,
            trading_day=context.trading_day,
            status=status,
            symbols_scanned=len(results),
            in_range=in_range,
            error_symbols=errors,
            insufficient_symbols=insuff,
        )

    def _skipped(
        self, scan_type: ScanType, now: datetime, trading_day: date, reason: str
    ) -> ScanSummary:
        return ScanSummary(
            scan_id=f"{trading_day}#{scan_type.value}#skipped",
            scan_type=scan_type,
            scheduled_at=now,
            ran_at=now,
            trading_day=trading_day,
            status=ScanStatus.SKIPPED,
            symbols_scanned=0,
            in_range=(),
            error_symbols=(),
            insufficient_symbols=(),
            notes=reason,
        )

    def _aborted(
        self, context: ScanContext, universe: list[str], reason: str
    ) -> ScanSummary:
        # Not persisted: an aborted scan must not become the diff baseline (empty in_range would
        # make the next scan report everything as "Entered"). It is effectively a missed scan,
        # which the next scan's gap check reports.
        return ScanSummary(
            scan_id=context.scan_id,
            scan_type=context.scan_type,
            scheduled_at=context.ran_at,
            ran_at=context.ran_at,
            trading_day=context.trading_day,
            status=ScanStatus.ABORTED,
            symbols_scanned=len(universe),
            in_range=(),
            error_symbols=(),
            insufficient_symbols=(),
            notes=reason,
        )

    # ---------------------------------------------------------------- helpers
    def _send(self, message: str) -> DeliveryStatus:
        try:
            return self._notifier.send(message)
        except Exception:  # noqa: BLE001 — a failed alert never crashes the scan (FR-5)
            log.exception("notifier raised; treating as FAILED")
            return DeliveryStatus.FAILED

    def _notify_system(self, text: str) -> None:
        self._send(f"{_SYSTEM_PREFIX} {text}")

    def _check_missed_scans(self, context: ScanContext) -> None:
        """Emit ``⚠️ SYSTEM missed <TYPE>`` for any scheduled scan earlier in the day that
        never ran (architecture §8.5 / failure taxonomy row 10). Only the first scan to run
        after a gap reports it, so a missing PRE is flagged once — not again by CLOSE."""
        if context.scan_type is ScanType.MANUAL:
            return
        times = self._cfg.scheduled_times
        current_time = times.get(context.scan_type)
        if current_time is None:
            return

        recorded_types = {s.scan_type for s in self._repo.scans_on(context.trading_day)}
        for stype, t in times.items():
            if t >= current_time or stype in recorded_types:
                continue
            # Dedupe: if a later-scheduled scan already ran, it has already flagged this gap.
            if any(times.get(rt, t) > t for rt in recorded_types):
                continue
            self._notify_system(f"missed {stype.value}")


def _result(symbol: str, status: SymbolStatus) -> SymbolScanResult:
    return SymbolScanResult(
        symbol=symbol,
        status=status,
        price=None,
        indicators=None,
        distance_atr=None,
        in_range=False,
    )
