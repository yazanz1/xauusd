"""After NY open (Israel clock): block London-color entries.

From 15:30 Asia/Jerusalem inclusive, only `yellow` (daily) and `ny`
(NY/orange) may open. `london` is rejected. Before 15:30 all colors pass.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from filters import FilterResult

TZ = ZoneInfo("Asia/Jerusalem")
NAME = "london_after_ny"
CUTOFF = time(15, 30)
ALLOWED_AFTER = frozenset({"yellow", "ny"})


def _local_now(now: datetime | None) -> datetime:
    return (now or datetime.now(TZ)).astimezone(TZ)


def after_ny_cutoff(now: datetime | None = None) -> bool:
    return _local_now(now).time() >= CUTOFF


class LondonAfterNyFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        now = context.get("now") if context else None
        if not after_ny_cutoff(now):
            return FilterResult(allowed=True)

        color = (signal.get("color") or "").strip().lower()
        if color in ALLOWED_AFTER:
            return FilterResult(allowed=True)

        local = _local_now(now)
        return FilterResult(
            allowed=False,
            reason=(
                f"after {CUTOFF.strftime('%H:%M')} Asia/Jerusalem only yellow/ny "
                f"(now {local.strftime('%H:%M')}, color={color or 'missing'})"
            ),
        )
