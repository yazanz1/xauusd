"""Signal filters for xaubot.

Each filter is its own module under filters/. The bot runs active_filters()
before opening a signal. Do not invent trading rules; only implement what is asked.
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
    """Filter chain used before MT5 open."""
    from filters.israel_hours import IsraelHoursFilter
    from filters.london_after_ny import LondonAfterNyFilter
    from filters.ny_open import NyOpenFilter
    from filters.us_holiday_next_day import UsHolidayNextDayFilter

    return [
        IsraelHoursFilter(),
        LondonAfterNyFilter(),
        UsHolidayNextDayFilter(),
        NyOpenFilter(),
    ]
