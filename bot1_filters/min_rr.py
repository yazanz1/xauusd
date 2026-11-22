"""G — block when reward/risk < MIN_RR (Pine entry/stop/tp).

Default mode is off. Env: FILTER_MIN_RR_MODE, MIN_RR=1.0.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from bot1_filters import FilterResult
from bot1_filters._mode import apply, mode

NAME = "min_rr"
log = logging.getLogger("xaubot")


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MinRrFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        m = mode("FILTER_MIN_RR_MODE", "off")
        if m == "off":
            return FilterResult(allowed=True)

        try:
            entry = _f(signal.get("entry"))
            stop = _f(signal.get("stop"))
            tp = _f(signal.get("tp"))
            if entry is None or stop is None or tp is None:
                log.warning("%s: missing entry/stop/tp — allowed", NAME)
                return FilterResult(allowed=True)

            risk = abs(entry - stop)
            reward = abs(tp - entry)
            if risk <= 0:
                log.warning("%s: risk=0 — allowed", NAME)
                return FilterResult(allowed=True)

            min_rr = float(os.getenv("MIN_RR") or "1.0")
            rr = reward / risk
            if rr >= min_rr:
                return FilterResult(allowed=True)

            reason = f"rr {rr:.2f} < {min_rr:.2f} (reward {reward:.2f} / risk {risk:.2f})"
            return apply(NAME, m, False, reason, signal.get("id"))
        except Exception as exc:
            log.warning("%s: error %s — allowed", NAME, exc)
            return FilterResult(allowed=True)
