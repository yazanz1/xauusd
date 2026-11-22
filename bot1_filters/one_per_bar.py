"""F — one MT5 open per (entry_time, direction).

Bot passes context["same_bar_open"]=True when another row already has
the same entry_time + direction with mt5_ticket set.
Env: FILTER_ONE_PER_BAR_MODE (shadow/block/off).
"""

from __future__ import annotations

import logging
from typing import Any

from bot1_filters import FilterResult
from bot1_filters._mode import apply, mode

NAME = "one_per_bar"
log = logging.getLogger("xaubot")


class OnePerBarFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        m = mode("FILTER_ONE_PER_BAR_MODE", "shadow")
        if m == "off":
            return FilterResult(allowed=True)

        try:
            if context is None or "same_bar_open" not in context:
                log.warning("%s: missing same_bar_open — allowed", NAME)
                return FilterResult(allowed=True)

            if not context["same_bar_open"]:
                return FilterResult(allowed=True)

            direction = (signal.get("direction") or "").strip().lower() or "?"
            entry_time = signal.get("entry_time") or "?"
            reason = f"{direction} @{entry_time} already open"
            return apply(NAME, m, False, reason, signal.get("id"))
        except Exception as exc:
            log.warning("%s: error %s — allowed", NAME, exc)
            return FilterResult(allowed=True)
