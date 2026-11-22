"""H1 EMA50 side filter — enter only AGAINST the price's side of the H1 EMA50.

Rule (added 2026-11-07 18:34 IDT):
- Take the last COMPLETED H1 bar before the hour that contains the signal bar
  (signal `entry_time` = open time of the 5m signal bar, as sent by Pine).
- side = H1 close - EMA50(H1 close) on that bar.
- long  allowed only if side < 0  (price below H1 EMA50)
- short allowed only if side > 0  (price above H1 EMA50)
- side == 0, or long above / short below the EMA -> rejected ("with" side).

Backtest (OANDA:XAUUSD, MT5 fills, 0.1 lot, 2026-09-14..2026-11-06, lock 40/35):
all 762 trades -2,564.53$ -> 313 allowed trades +1,886.14$; the rejected ("with")
group lost in every one of the 8 weeks.

Mode via env H1_EMA_MODE:
- "shadow" (default): never blocks; logs what it WOULD block (prefix SHADOW).
- "block": rejects "with"-side signals (reason goes to mt5_error).
- "off": filter does nothing.

Data comes from the MT5 terminal the bot is already connected to
(MetaTrader5 module, symbol from MT5_SYMBOL). On any data problem the filter
FAILS OPEN (allows) and logs a warning — it never blocks because of missing data.

Registered in bot1_filters.active_filters() (last in chain). Default mode is shadow
until H1_EMA_MODE=block is set in .env.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

from bot1_filters import FilterResult

NAME = "h1_ema_side"
EMA_LEN = 50
BARS = 600  # history for EMA warm-up (SMA seed like Pine ta.ema)

log = logging.getLogger("xaubot")


def _mode() -> str:
    m = (os.getenv("H1_EMA_MODE") or "shadow").strip().lower()
    return m if m in ("off", "shadow", "block") else "shadow"


def _parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ema_last(closes: list[float], length: int = EMA_LEN) -> float | None:
    """EMA of the series, seeded with the SMA of the first `length` values (Pine ta.ema)."""
    if len(closes) < length:
        return None
    k = 2.0 / (length + 1)
    e = sum(closes[:length]) / length
    for c in closes[length:]:
        e += k * (c - e)
    return e


def h1_side(entry_utc: datetime, now_utc: datetime | None = None) -> tuple[float, float, float] | None:
    """Return (side, h1_close, ema50) for the last completed H1 bar before the signal's hour.

    Bar position: 0 = forming H1 bar now. The wanted bar is the one before the hour
    of `entry_utc`, i.e. position 1 + (hours elapsed since that hour).
    """
    import MetaTrader5 as mt5  # already initialized by the bot's MT5Client

    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hours_since = int(now_utc.timestamp() // 3600) - int(entry_utc.timestamp() // 3600)
    if hours_since < 0 or hours_since > 3:
        log.warning("%s: unexpected signal age (%s h) — skipping check", NAME, hours_since)
        return None
    pos = 1 + hours_since

    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, pos, BARS)
    if rates is None or len(rates) < EMA_LEN + 10:
        log.warning("%s: no H1 rates for %s (%s)", NAME, symbol, mt5.last_error())
        return None

    closes = [float(r["close"]) for r in rates]
    ema = ema_last(closes)
    if ema is None or not math.isfinite(ema):
        return None
    close = closes[-1]
    return close - ema, close, ema


class H1EmaSideFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        mode = _mode()
        if mode == "off":
            return FilterResult(allowed=True)

        direction = (signal.get("direction") or "").strip().lower()
        entry_utc = _parse_utc(signal.get("entry_time"))
        if direction not in ("long", "short") or entry_utc is None:
            log.warning("%s: missing direction/entry_time — allowed", NAME)
            return FilterResult(allowed=True)

        now = context.get("now") if context else None
        try:
            res = h1_side(entry_utc, now)
        except Exception as exc:  # fail open
            log.warning("%s: error %s — allowed", NAME, exc)
            return FilterResult(allowed=True)
        if res is None:
            return FilterResult(allowed=True)

        side, close, ema = res
        allowed = side < 0 if direction == "long" else side > 0
        if allowed:
            return FilterResult(allowed=True)

        reason = (
            f"{direction} on same side as H1 EMA50 "
            f"(H1 close {close:.2f} vs EMA50 {ema:.2f}, side {side:+.2f})"
        )
        if mode == "shadow":
            log.warning("SHADOW %s would block %s: %s", NAME, signal.get("id"), reason)
            return FilterResult(allowed=True)
        return FilterResult(allowed=False, reason=reason)
