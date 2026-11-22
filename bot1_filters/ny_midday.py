"""E — no new entries during NY midday [12:00, 14:00) America/New_York.

Uses signal time from context["now"] (created_at) when provided.
Env: FILTER_NY_MIDDAY_MODE (shadow/block/off), NY_MIDDAY_START, NY_MIDDAY_END.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from bot1_filters import FilterResult
from bot1_filters._mode import apply, mode

TZ = ZoneInfo("America/New_York")
NAME = "ny_midday"
log = logging.getLogger("xaubot")


def _parse_hhmm(raw: str, fallback: time) -> time:
    try:
        parts = raw.strip().split(":")
        return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError, AttributeError):
        return fallback


def _window() -> tuple[time, time]:
    start = _parse_hhmm(os.getenv("NY_MIDDAY_START") or "12:00", time(12, 0))
    end = _parse_hhmm(os.getenv("NY_MIDDAY_END") or "14:00", time(14, 0))
    return start, end


class NyMiddayFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        m = mode("FILTER_NY_MIDDAY_MODE", "shadow")
        if m == "off":
            return FilterResult(allowed=True)

        try:
            now = context.get("now") if context else None
            if now is None:
                now = datetime.now(TZ)
            elif isinstance(now, datetime):
                now = now if now.tzinfo else now.replace(tzinfo=ZoneInfo("UTC"))
                now = now.astimezone(TZ)
            else:
                log.warning("%s: bad now — allowed", NAME)
                return FilterResult(allowed=True)

            local = now.astimezone(TZ).time()
            start, end = _window()
            blocked = start <= local < end
            reason = f"{local.strftime('%H:%M')} NY in [{start.strftime('%H:%M')},{end.strftime('%H:%M')})"
            return apply(NAME, m, not blocked, reason, signal.get("id"))
        except Exception as exc:
            log.warning("%s: error %s — allowed", NAME, exc)
            return FilterResult(allowed=True)
