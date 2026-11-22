"""Signal filters for xaubot (package bot1_filters).

Each filter is its own module. The bot runs active_filters() before opening.
Do not invent trading rules; only implement what is asked.

Filter modules under this package are kept for reference / future use.
For wick (+60) the bot never loads them — Pine owns entry filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class FilterResult:
    """Outcome of one filter check."""

    allowed: bool
    reason: str = ""


class SignalFilter(Protocol):
    """A single gate before opening a signal."""

    name: str

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        """Return allowed=False to skip the signal (with a short reason)."""
        ...


def run_filters(
    signal: dict[str, Any],
    filters: list[SignalFilter],
    *,
    context: dict[str, Any] | None = None,
) -> FilterResult:
    """Run filters in order; stop on the first rejection."""
    for filt in filters:
        result = filt.check(signal, context=context)
        if not result.allowed:
            reason = result.reason or filt.name
            return FilterResult(allowed=False, reason=f"{filt.name}: {reason}")
    return FilterResult(allowed=True)


def active_filters() -> list[SignalFilter]:
    """Always empty for wick (+60). Does not read STRATEGY from .env.

    Pine already applies F1+F3+F4+F5 / hours / news / holidays.
    Filter modules remain in this package but are not loaded.
    """
    return []
