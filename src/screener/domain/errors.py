"""Typed exceptions. The core raises these; adapters translate provider/storage errors into
them at the edge so nothing SDK-shaped escapes (architecture §5.1)."""

from __future__ import annotations


class ScreenerError(Exception):
    """Base for all domain errors."""


class ScanAborted(ScreenerError):
    """A scan-scoped failure that must not degrade into a plausible-looking alert (§8.3):
    staleness guard tripped, or >50% of the universe failed to fetch."""


class StaleDataError(ScanAborted):
    """The newest daily bar is older than the last expected trading day (FR-2, §7.2)."""


class ProviderError(ScreenerError):
    """A provider call failed after its internal retries. Scan-scoped only — per-symbol
    failures are reported as data (SymbolStatus.DATA_ERROR), never raised."""


class RepositoryError(ScreenerError):
    """A storage write/read failed. Repository write failures fail loudly (§8.3)."""


class ConfigError(ScreenerError):
    """Invalid or missing configuration."""


class UnknownSymbolError(ScreenerError):
    """A dossier was requested for a symbol no provider recognises (PRD §5): the caller turns
    this into a friendly rejection rather than a stack trace."""
