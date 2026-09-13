"""NY cash open window — no new entries 15:20–15:30 IDT every day.

Blocks thin/fake liquidity around NY equity open. Open positions continue.
Half-open: [15:20, 15:30) Asia/Jerusalem.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from filters import FilterResult

TZ = ZoneInfo("Asia/Jerusalem")
NAME = "ny_open"
START = time(15, 20)
END = time(15, 30)


def is_ny_open_blackout(now: datetime | None = None) -> bool:
    local = (now or datetime.now(TZ)).astimezone(TZ).time()
    return START <= local < END


class NyOpenFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        now = context.get("now") if context else None
        if not is_ny_open_blackout(now):
            return FilterResult(allowed=True)
        local = (now or datetime.now(TZ)).astimezone(TZ)
        return FilterResult(
            allowed=False,
            reason=f"NY open blackout {local.strftime('%H:%M')} IDT (15:20-15:30)",
        )
