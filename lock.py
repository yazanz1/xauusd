"""40/35 lock manager — bot-side lock (aligned with current strategy).

States: none → pend → locked

- Formula uses webhook `entry` + `tp` (not mt5_fill_price) for 1:1 with the journal.
- On a closed M5 bar that first touches 40% of entry→tp → pend + compute lockSL at 35%.
- At the start of the next M5 bar (first POLL after that bar opens) → set_sl(lockSL).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from mt5_client import MT5Client

log = logging.getLogger("xaubot.lock")

LOCK_HIT_FRAC = 0.4
LOCK_SL_FRAC = 0.35


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _bar_time(bar) -> datetime:
    return datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)


def lock_sl_price(direction: str, entry: float, tp: float) -> float:
    dist = abs(tp - entry)
    if direction == "long":
        return entry + LOCK_SL_FRAC * dist
    return entry - LOCK_SL_FRAC * dist


def hit_lock_level(direction: str, entry: float, tp: float, high: float, low: float) -> bool:
    dist = abs(tp - entry)
    if dist <= 0:
        return False
    if direction == "long":
        return high >= entry + LOCK_HIT_FRAC * dist
    return low <= entry - LOCK_HIT_FRAC * dist


# Back-compat alias
hit_50 = hit_lock_level


def manage_lock(
    trade: dict[str, Any],
    mt5: MT5Client,
    *,
    update_trade: Callable[[str, dict[str, Any]], None],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Advance lock state for one open copied trade. Returns updated local fields."""
    if not os.getenv("LOCK_ENABLE", "1").strip().lower() in ("1", "true", "yes", "on"):
        return trade

    ticket = trade.get("mt5_ticket")
    if ticket is None:
        return trade

    direction = (trade.get("direction") or "").lower()
    entry = _to_float(trade.get("entry"))  # webhook journal entry
    tp = _to_float(trade.get("tp"))
    if direction not in ("long", "short") or entry is None or tp is None:
        return trade

    state = (trade.get("lock_state") or "none").lower()
    if state == "locked":
        return trade

    pos = mt5.get_position(int(ticket))
    if pos is None:
        return trade

    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    bars = mt5.m5_bars(symbol, count=5)
    if len(bars) < 2:
        return trade

    # bars[-1] = current forming, bars[-2] = last closed
    closed = bars[-2]
    forming = bars[-1]
    closed_t = _bar_time(closed)
    forming_t = _bar_time(forming)

    trade_id = trade["id"]
    orig_stop = _to_float(trade.get("orig_stop"))
    if orig_stop is None:
        orig_stop = _to_float(trade.get("stop")) or float(pos.sl or 0) or None

    # --- none: detect 40% on last closed M5 bar ---
    if state == "none":
        high = float(closed["high"])
        low = float(closed["low"])
        if not hit_lock_level(direction, entry, tp, high, low):
            if orig_stop is not None and trade.get("orig_stop") is None:
                try:
                    update_trade(trade_id, {"orig_stop": orig_stop})
                    trade = {**trade, "orig_stop": orig_stop}
                except Exception:
                    log.debug("orig_stop column missing", exc_info=True)
            return trade

        lock_sl = mt5.normalize_price(lock_sl_price(direction, entry, tp), symbol)
        payload = {
            "lock_state": "pend",
            "lock_sl": lock_sl,
            "lock_pend_bar_time": closed_t.isoformat(),
            "orig_stop": orig_stop,
        }
        try:
            update_trade(trade_id, payload)
        except Exception:
            log.exception("Could not write lock pend for %s", trade_id)
            return trade
        log.info(
            "LockPend ticket=%s bar=%s lockSL=%s (40%% hit on closed M5)",
            ticket,
            closed_t.isoformat(),
            lock_sl,
        )
        return {**trade, **payload}

    # --- pend: apply SL at start of next M5 bar ---
    if state == "pend":
        pend_bar = trade.get("lock_pend_bar_time")
        if not pend_bar:
            return trade
        pend_t = datetime.fromisoformat(str(pend_bar).replace("Z", "+00:00"))
        if pend_t.tzinfo is None:
            pend_t = pend_t.replace(tzinfo=timezone.utc)
        # Next bar has started when forming bar time > pend bar time
        if forming_t <= pend_t:
            return trade

        lock_sl = _to_float(trade.get("lock_sl"))
        if lock_sl is None:
            lock_sl = mt5.normalize_price(lock_sl_price(direction, entry, tp), symbol)

        if dry_run:
            log.info("DRY_RUN would set_sl ticket=%s → %s", ticket, lock_sl)
            payload = {"lock_state": "locked", "lock_sl": lock_sl, "stop": lock_sl}
            try:
                update_trade(trade_id, payload)
            except Exception:
                log.debug("lock columns missing on dry_run write", exc_info=True)
            return {**trade, **payload}

        result = mt5.set_sl(int(ticket), lock_sl)
        if not result.ok:
            log.error("set_sl failed ticket=%s: %s", ticket, result.error or result.comment)
            try:
                update_trade(trade_id, {"mt5_error": f"lock set_sl failed: {result.error or result.comment}"[:500]})
            except Exception:
                pass
            return trade

        payload = {
            "lock_state": "locked",
            "lock_sl": lock_sl,
            "stop": result.sl if result.sl is not None else lock_sl,
            "mt5_error": None,
        }
        try:
            update_trade(trade_id, payload)
        except Exception:
            # Fallback without lock columns
            try:
                update_trade(trade_id, {"stop": payload["stop"], "mt5_error": None})
            except Exception:
                log.exception("Could not write locked stop for %s", trade_id)
            return {**trade, "lock_state": "locked", "lock_sl": lock_sl, "stop": payload["stop"]}

        log.info("Locked ticket=%s SL→%s at start of bar %s", ticket, lock_sl, forming_t.isoformat())
        return {**trade, **payload}

    return trade


def resolve_exit_reason(
    trade: dict[str, Any],
    *,
    exit_price: float,
    mt5_reason: str,
) -> str:
    """Map MT5 close to journal exit_reason; prefer lock when SL hit after lock."""
    if mt5_reason == "tp":
        return "tp"

    lock_state = (trade.get("lock_state") or "none").lower()
    lock_sl = _to_float(trade.get("lock_sl"))
    orig = _to_float(trade.get("orig_stop"))

    if lock_state == "locked" and lock_sl is not None:
        if abs(exit_price - lock_sl) <= abs(exit_price - (orig or lock_sl)) + 0.05:
            return "lock"
        return "lock"

    if mt5_reason == "sl":
        return "sl"
    return mt5_reason
