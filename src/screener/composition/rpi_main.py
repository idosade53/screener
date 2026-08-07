"""Long-running composition root (the RPi5 / local-daemon shape). Wires the same core behind
APScheduler and a Telegram long-poll loop. Scheduler *correctness* (weekend/holiday/half-day) is
M6 and the Telegram transport is M4/M5 — this module is the assembly point that proves the
run-target exists and that nothing in the core changes between Lambda and a long-running process
(architecture §9.5)."""

from __future__ import annotations

import logging

from screener.composition.wiring import Application, build_application
from screener.domain.models import ScanType

log = logging.getLogger("screener.rpi")


def build_daemon() -> Application:
    """Assemble the application for a long-running process. Scheduling and the command loop are
    attached by :func:`run` once their adapters (APScheduler wiring, Telegram long-poll) land."""
    return build_application()


def run() -> None:  # pragma: no cover - exercised end-to-end, not in unit tests
    app = build_daemon()
    # TODO(M6): register APScheduler jobs for PRE/OPEN/CLOSE using app.settings.scheduled_times
    #           and app.calendar, with max_instances=1 and coalesce=True.
    # TODO(M4/M5): start the Telegram long-poll command loop.
    # For now, a daemon with no scheduler is a no-op; the CLI drives scans in Phase-1 core.
    log.info(
        "daemon assembled (backend=%s); scheduler/command-loop pending M4–M6",
        app.settings.repository_backend,
    )


def run_scan_now(scan_type: ScanType = ScanType.MANUAL) -> None:  # pragma: no cover
    build_daemon().pipeline().run(scan_type)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run()
