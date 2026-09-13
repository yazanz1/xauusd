"""No new entries during Israel blackout windows.

Blocked (Asia/Jerusalem, half-open):
- 12:00–14:30  → [12:00, 14:30)
- 22:00–00:00  → [22:00, 24:00)
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from filters import FilterResult

TZ = ZoneInfo("Asia/Jerusalem")
NAME = "israel_hours"

# (start inclusive, end exclusive) as clock times in Israel.
_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(12, 0), time(14, 30)),
    (time(22, 0), time(0, 0)),  # end 00:00 → through end of day
)


def _in_window(now: time, start: time, end: time) -> bool:
    if end == time(0, 0):
        return now >= start
    return start <= now < end


def is_blackout(now: datetime | None = None) -> bool:
    local = (now or datetime.now(TZ)).astimezone(TZ).time()
    return any(_in_window(local, start, end) for start, end in _WINDOWS)


class IsraelHoursFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        now = None
        if context and context.get("now") is not None:
            now = context["now"]
        if not is_blackout(now):
            return FilterResult(allowed=True)
        local = (now or datetime.now(TZ)).astimezone(TZ)
        return FilterResult(
            allowed=False,
            reason=f"blackout {local.strftime('%H:%M')} Asia/Jerusalem (12:00-14:30 or 22:00-00:00)",
        )
