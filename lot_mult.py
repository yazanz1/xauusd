"""Clamp wick webhook lot_mult before multiplying LOT_SIZE."""

from __future__ import annotations

import math
from typing import Any


def lot_mult(raw: Any) -> float:
    """Return a multiplier in [0.05, 1]. Missing, invalid, or NaN is 1.0.

    ``0`` is treated as missing (``or 1.0``), matching the open path in bot.py.
    """
    if raw is None or raw == "":
        return 1.0
    try:
        mult = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(mult) or mult == 0:
        return 1.0
    return min(1.0, max(0.05, mult))
