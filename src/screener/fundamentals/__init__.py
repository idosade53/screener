"""Phase 4 fundamentals core: the pure scorecard engine, dossier assembly and formatting.

Layered like ``screener/screener`` — this package knows nothing of providers, telegram or the
DB. It depends only on ``domain``/``ports``/``indicators`` (enforced by ``.importlinter``), so the
scorecard is directly unit-testable and reusable by any future surface (PRD §9, FR-4)."""
