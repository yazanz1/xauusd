"""Shared off / shadow / block mode for bot1 filters."""

from __future__ import annotations

import logging
import os

from bot1_filters import FilterResult

log = logging.getLogger("xaubot")


def mode(env_name: str, default: str = "shadow") -> str:
    m = (os.getenv(env_name) or default).strip().lower()
    return m if m in ("off", "shadow", "block") else default


def apply(
    name: str,
    mode_val: str,
    allowed: bool,
    reason: str,
    signal_id: object = None,
) -> FilterResult:
    if mode_val == "off" or allowed:
        return FilterResult(allowed=True)
    if mode_val == "shadow":
        log.warning("SHADOW %s would block %s: %s", name, signal_id, reason)
        return FilterResult(allowed=True)
    return FilterResult(allowed=False, reason=reason)
