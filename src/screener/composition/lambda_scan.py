"""``screener-scan`` Lambda handler (architecture §9.1). Invoked by three EventBridge Scheduler
rules (ET cron), each passing a constant ``{"scan_type": "PRE|OPEN|CLOSE"}`` input. Reserved
concurrency 1 plus the pipeline's deterministic claim (ADR A6) make retried invocations safe.

Thin by design: resolve settings (SSM on Lambda), build the app, run one scan. All the behaviour
— trading-day gate, staleness abort, diff, notify — lives in the shared pipeline unchanged."""

from __future__ import annotations

import logging
from typing import Any

from screener.composition.secrets import resolve_settings
from screener.composition.wiring import build_application
from screener.domain.errors import ConfigError
from screener.domain.models import ScanType

log = logging.getLogger("screener.lambda.scan")

# Build once per warm container: the app (and its DynamoDB client) is reused across invocations.
_app = None


def _scan_type(event: dict[str, Any]) -> ScanType:
    raw = event.get("scan_type")
    if not isinstance(raw, str):
        raise ConfigError("scan event is missing a string 'scan_type'")
    try:
        return ScanType(raw.upper())
    except ValueError as exc:
        raise ConfigError(f"unknown scan_type {raw!r}") from exc


def handler(event: dict[str, Any], context: object = None) -> dict[str, Any]:
    global _app
    if _app is None:
        _app = build_application(resolve_settings())
    scan_type = _scan_type(event or {})
    log.info("running %s scan", scan_type.value)
    summary = _app.pipeline().run(scan_type)
    return {
        "scan_id": summary.scan_id,
        "status": summary.status.value,
        "in_range": list(summary.in_range),
    }
