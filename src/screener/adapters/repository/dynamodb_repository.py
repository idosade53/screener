"""DynamoDB implementation of ``ScreenerRepository`` over the single ``screener`` table
(architecture §9.3, PRD §8.3). Attribute names are short because DynamoDB stores them on every
item. All five Phase-1 access patterns are served without a GSI.

Item shapes (``PK`` / ``SK``):

    UNIVERSE     / SYM#<sym>                   sym, added, active, validated
    BAR#<sym>    / <date>                       o, h, l, c, v
    IND#<sym>    / <asof>                        sma, atr, asof
    SCAN         / <scan_id>                     type, sched, ran, tday, status, n,
                                                 inrange[], errs[], insuf[], notes
    HIST#<sym>   / <ran_at>                      p, sma, atr, dist, in, status
    CLAIM        / <scan_id>                     claimed
    ALERT#<id>   / <sent_at>                     msg, status
    FUND#<sym>   / SNAPSHOT                     fetched_at, next_earnings_date, source, payload
    NEWS#<sym>   / LATEST                       fetched_at, source, payload

``latest_scan`` reads exactly one item (``Query PK=SCAN, ScanIndexForward=false, Limit 1``):
``scan_id`` is ``{trading_day}T{hhmm}Z#{TYPE}`` so SCAN items already sort chronologically. Claims
live under their own ``CLAIM`` partition so ``latest_scan``/``scans_on`` never observe them — the
same separation the SQLite adapter gets from a distinct ``scan_claims`` table (§8.4).

Numeric policy (ADR A11): ``Decimal`` in and out; boto3's resource layer maps DynamoDB ``Number``
to ``Decimal`` and rejects ``float`` outright, so the boundary is enforced for us.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from screener.adapters.repository._fundamentals_codec import (
    dumps_cached_fundamentals,
    dumps_news_items,
    loads_cached_fundamentals,
    loads_news_items,
)
from screener.domain.errors import RepositoryError
from screener.domain.models import (
    Bar,
    CachedFundamentals,
    DeliveryStatus,
    Indicators,
    NewsCacheEntry,
    ScanStatus,
    ScanSummary,
    ScanType,
    SymbolScanResult,
    UniverseMember,
)

_UNIVERSE_PK = "UNIVERSE"
_SCAN_PK = "SCAN"
_CLAIM_PK = "CLAIM"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


class DynamoDbScreenerRepository:
    """Single-table repository. The pipeline's single-writer model (reserved concurrency 1 /
    deterministic claim) means no cross-item transaction is required."""

    def __init__(self, table: Any) -> None:
        # ``table`` is a boto3 DynamoDB ``Table`` resource. Injected so tests can pass a
        # moto-backed table and the composition root the real one.
        self._table = table

    # ------------------------------------------------------------ query helper
    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Run a query, following ``LastEvaluatedKey`` pagination to completion."""
        items: list[dict[str, Any]] = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start:
                return items
            kwargs["ExclusiveStartKey"] = start

    # ------------------------------------------------------------------ universe
    def get_universe(self) -> list[UniverseMember]:
        rows = self._query_all(
            KeyConditionExpression=Key("PK").eq(_UNIVERSE_PK),
            FilterExpression=Attr("active").eq(True),
        )
        members = [
            UniverseMember(
                symbol=r["sym"],
                added_at=datetime.fromisoformat(r["added"]),
                active=bool(r["active"]),
                last_validated_at=(
                    datetime.fromisoformat(r["validated"]) if r.get("validated") else None
                ),
            )
            for r in rows
        ]
        return sorted(members, key=lambda m: m.symbol)

    def add_symbols(self, symbols: Sequence[str]) -> None:
        now = _now_iso()
        for sym in symbols:
            # Reactivate on re-add, preserving the original ``added`` timestamp (idempotent, §8.4).
            self._table.update_item(
                Key={"PK": _UNIVERSE_PK, "SK": f"SYM#{sym}"},
                UpdateExpression="SET active = :t, sym = :s, added = if_not_exists(added, :now)",
                ExpressionAttributeValues={":t": True, ":s": sym, ":now": now},
            )

    def remove_symbol(self, symbol: str) -> None:
        # Soft delete (ADR A12). A missing symbol is a no-op, matching SQLite's UPDATE semantics.
        try:
            self._table.update_item(
                Key={"PK": _UNIVERSE_PK, "SK": f"SYM#{symbol}"},
                UpdateExpression="SET active = :f",
                ExpressionAttributeValues={":f": False},
                ConditionExpression=Attr("PK").exists(),
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return
            raise RepositoryError(f"remove_symbol failed for {symbol}: {exc}") from exc

    # ---------------------------------------------------------------------- bars
    def get_bars(self, symbol: str, since: date) -> list[Bar]:
        rows = self._query_all(
            KeyConditionExpression=(
                Key("PK").eq(f"BAR#{symbol}") & Key("SK").gte(since.isoformat())
            )
        )
        return [
            Bar(
                date=date.fromisoformat(r["SK"]),
                open=_dec(r["o"]),
                high=_dec(r["h"]),
                low=_dec(r["l"]),
                close=_dec(r["c"]),
                volume=int(r["v"]),
            )
            for r in rows
        ]

    def upsert_bars(self, symbol: str, bars: Sequence[Bar]) -> None:
        with self._table.batch_writer() as batch:
            for b in bars:
                batch.put_item(
                    Item={
                        "PK": f"BAR#{symbol}",
                        "SK": b.date.isoformat(),
                        "o": b.open,
                        "h": b.high,
                        "l": b.low,
                        "c": b.close,
                        "v": b.volume,
                    }
                )

    def latest_bar_date(self, symbol: str) -> date | None:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(f"BAR#{symbol}"),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return date.fromisoformat(items[0]["SK"]) if items else None

    def delete_bars(self, symbol: str) -> None:
        # Bars and the derived indicator cache go together (a re-fetch invalidates both, §7.3).
        for pk in (f"BAR#{symbol}", f"IND#{symbol}"):
            keys = self._query_all(
                KeyConditionExpression=Key("PK").eq(pk),
                ProjectionExpression="PK, SK",
            )
            with self._table.batch_writer() as batch:
                for k in keys:
                    batch.delete_item(Key={"PK": k["PK"], "SK": k["SK"]})

    # ---------------------------------------------------------- indicator cache
    def get_indicators(
        self, symbols: Sequence[str], asof: date
    ) -> dict[str, Indicators]:
        # Keyed on the exact ``asof``, so a stale cache from a prior day is simply absent — the
        # asof-mismatch guard (§7.4) falls out of the key design rather than a filter.
        out: dict[str, Indicators] = {}
        for sym in symbols:
            resp = self._table.get_item(Key={"PK": f"IND#{sym}", "SK": asof.isoformat()})
            item = resp.get("Item")
            if item is None:
                continue
            out[sym] = Indicators(
                sma150=_dec(item["sma"]),
                atr14=_dec(item["atr"]),
                asof=date.fromisoformat(item["asof"]),
            )
        return out

    def put_indicators(self, values: Mapping[str, Indicators]) -> None:
        with self._table.batch_writer() as batch:
            for sym, ind in values.items():
                batch.put_item(
                    Item={
                        "PK": f"IND#{sym}",
                        "SK": ind.asof.isoformat(),
                        "sma": ind.sma150,
                        "atr": ind.atr14,
                        "asof": ind.asof.isoformat(),
                    }
                )

    # --------------------------------------------------------------------- scans
    def latest_scan(self) -> ScanSummary | None:
        resp = self._table.query(
            KeyConditionExpression=Key("PK").eq(_SCAN_PK),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return _summary_from_item(items[0]) if items else None

    def scans_on(self, day: date) -> list[ScanSummary]:
        # Filter on the ``tday`` attribute rather than an SK prefix: a scan's trading day is not
        # always its wall-clock date (a CLOSE at 20:15 ET straddles UTC midnight).
        rows = self._query_all(
            KeyConditionExpression=Key("PK").eq(_SCAN_PK),
            FilterExpression=Attr("tday").eq(day.isoformat()),
        )
        summaries = [_summary_from_item(r) for r in rows]
        return sorted(summaries, key=lambda s: s.ran_at)

    def save_scan(
        self, summary: ScanSummary, results: Sequence[SymbolScanResult]
    ) -> None:
        try:
            item: dict[str, Any] = {
                "PK": _SCAN_PK,
                "SK": summary.scan_id,
                "type": summary.scan_type.value,
                "sched": summary.scheduled_at.isoformat(),
                "ran": summary.ran_at.isoformat(),
                "tday": summary.trading_day.isoformat(),
                "status": summary.status.value,
                "n": summary.symbols_scanned,
                "inrange": list(summary.in_range),
                "errs": list(summary.error_symbols),
                "insuf": list(summary.insufficient_symbols),
            }
            if summary.notes is not None:
                item["notes"] = summary.notes
            self._table.put_item(Item=item)

            # Per-symbol observations carry price/sma/atr, not just the boolean, so Phase 2 can
            # replay what was actually observed (extension point 11.3). Batched 25/call (§8.3).
            with self._table.batch_writer() as batch:
                for r in results:
                    batch.put_item(Item=_observation_item(summary, r))
        except ClientError as exc:  # a repository write failure must fail loudly (§8.3)
            raise RepositoryError(f"save_scan failed for {summary.scan_id}: {exc}") from exc

    def try_claim_scan(self, scan_id: str) -> bool:
        # Conditional put on a distinct CLAIM item: the first invocation wins, retries see the
        # condition fail (idempotency, ADR A6).
        try:
            self._table.put_item(
                Item={"PK": _CLAIM_PK, "SK": scan_id, "claimed": _now_iso()},
                ConditionExpression=Attr("SK").not_exists(),
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise RepositoryError(f"try_claim_scan failed for {scan_id}: {exc}") from exc

    # -------------------------------------------------------------------- alerts
    def record_alert(
        self, scan_id: str, message: str, status: DeliveryStatus
    ) -> None:
        now = _now_iso()
        self._table.put_item(
            Item={
                "PK": f"ALERT#{scan_id}",
                "SK": now,
                "msg": message,
                "status": status.value,
            }
        )

    # ------------------------------------------------- fundamentals & news cache
    # Latest-only per symbol under a fixed SK (SNAPSHOT / LATEST), so a put overwrites (PRD §10).
    # The derived metrics ride as a JSON ``payload`` string — the same codec the SQLite adapter
    # uses — so both stores are byte-identical (PRD FR-6).
    def get_fundamentals_snapshot(self, symbol: str) -> CachedFundamentals | None:
        item = self._table.get_item(
            Key={"PK": f"FUND#{symbol}", "SK": "SNAPSHOT"}
        ).get("Item")
        return loads_cached_fundamentals(item["payload"]) if item else None

    def put_fundamentals_snapshot(self, cached: CachedFundamentals) -> None:
        snap = cached.snapshot
        item: dict[str, Any] = {
            "PK": f"FUND#{snap.symbol}",
            "SK": "SNAPSHOT",
            "fetched_at": snap.fetched_at.isoformat(),
            "source": snap.source,
            "payload": dumps_cached_fundamentals(cached),
        }
        if snap.next_earnings_date is not None:
            item["next_earnings_date"] = snap.next_earnings_date.isoformat()
        self._table.put_item(Item=item)

    def get_news_cache(self, symbol: str) -> NewsCacheEntry | None:
        item = self._table.get_item(Key={"PK": f"NEWS#{symbol}", "SK": "LATEST"}).get("Item")
        if item is None:
            return None
        return NewsCacheEntry(
            symbol=symbol,
            fetched_at=datetime.fromisoformat(item["fetched_at"]),
            source=item["source"],
            items=loads_news_items(item["payload"]),
        )

    def put_news_cache(self, entry: NewsCacheEntry) -> None:
        self._table.put_item(
            Item={
                "PK": f"NEWS#{entry.symbol}",
                "SK": "LATEST",
                "fetched_at": entry.fetched_at.isoformat(),
                "source": entry.source,
                "payload": dumps_news_items(entry.items),
            }
        )


# ---------------------------------------------------------------------- helpers
def _dec(value: Any) -> Decimal:
    # DynamoDB Numbers already arrive as Decimal; normalise defensively.
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _is_conditional_failure(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return bool(code == "ConditionalCheckFailedException")


def _observation_item(summary: ScanSummary, r: SymbolScanResult) -> dict[str, Any]:
    item: dict[str, Any] = {
        "PK": f"HIST#{r.symbol}",
        "SK": summary.ran_at.isoformat(),
        "in": r.in_range,
        "status": r.status.value,
    }
    if r.price is not None:
        item["p"] = r.price
    if r.indicators is not None:
        item["sma"] = r.indicators.sma150
        item["atr"] = r.indicators.atr14
    if r.distance_atr is not None:
        item["dist"] = r.distance_atr
    return item


def _summary_from_item(item: Mapping[str, Any]) -> ScanSummary:
    return ScanSummary(
        scan_id=item["SK"],
        scan_type=ScanType(item["type"]),
        scheduled_at=datetime.fromisoformat(item["sched"]),
        ran_at=datetime.fromisoformat(item["ran"]),
        trading_day=date.fromisoformat(item["tday"]),
        status=ScanStatus(item["status"]),
        symbols_scanned=int(item["n"]),
        in_range=tuple(item["inrange"]),
        error_symbols=tuple(item["errs"]),
        insufficient_symbols=tuple(item["insuf"]),
        notes=item.get("notes"),
    )
