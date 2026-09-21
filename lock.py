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


def clamp_lock_sl_to_broker(
    direction: str,
    lock_sl: float,
    *,
    price: float,
    min_dist: float,
    fill_price: float,
) -> float:
    """Pull SL away from the market, but never through the fill.

    Call only when lock_sl is already on the profit side of price.
    Long: SL stays below bid and at or above mt5_fill_price.
    Short: SL stays above ask and at or below mt5_fill_price.
    """
    gap = max(min_dist * 1.2, 0.0)
    if direction == "long":
        adjusted = lock_sl if gap <= 0 else min(lock_sl, price - gap)
        return max(adjusted, fill_price)
    adjusted = lock_sl if gap <= 0 else max(lock_sl, price + gap)
    return min(adjusted, fill_price)


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
            lock_sl = lock_sl_price(direction, entry, tp)

        tick = mt5.tick(symbol)
        bid = float(tick.bid)
        ask = float(tick.ask)
        price = bid if direction == "long" else ask
        min_dist = float(mt5.min_stop_distance(symbol))
        raw_sl = float(lock_sl)
        fill = _to_float(trade.get("mt5_fill_price"))
        if fill is None:
            log.info(
                "Skip lock set_sl ticket=%s: missing mt5_fill_price — retry next poll",
                ticket,
            )
            return trade

        # Wrong side before clamp. Clamping first can shove SL through the fill
        # and stop the position out at a loss (ticket 2057249374).
        if direction == "long" and raw_sl >= bid:
            log.info(
                "Skip lock set_sl ticket=%s: long SL not below bid before clamp "
                "(sl=%s bid=%s ask=%s fill=%s) — retry next poll",
                ticket,
                raw_sl,
                bid,
                ask,
                fill,
            )
            return trade
        if direction == "short" and raw_sl <= ask:
            log.info(
                "Skip lock set_sl ticket=%s: short SL not above ask before clamp "
                "(sl=%s bid=%s ask=%s fill=%s) — retry next poll",
                ticket,
                raw_sl,
                bid,
                ask,
                fill,
            )
            return trade

        lock_sl = clamp_lock_sl_to_broker(
            direction, raw_sl, price=price, min_dist=min_dist, fill_price=fill
        )
        lock_sl = mt5.normalize_price(lock_sl, symbol)
        gap = abs(price - lock_sl)

        if direction == "long" and not (lock_sl < bid):
            log.info(
                "Skip lock set_sl ticket=%s: clamped long SL not below bid "
                "(sl=%s bid=%s fill=%s) — retry next poll",
                ticket,
                lock_sl,
                bid,
                fill,
            )
            return trade
        if direction == "short" and not (lock_sl > ask):
            log.info(
                "Skip lock set_sl ticket=%s: clamped short SL not above ask "
                "(sl=%s ask=%s fill=%s) — retry next poll",
                ticket,
                lock_sl,
                ask,
                fill,
            )
            return trade

        if min_dist > 0 and gap < min_dist:
            log.info(
                "Skip lock set_sl ticket=%s: gap=%.2f < min_dist=%.2f "
                "(price=%.2f raw_sl=%s adj_sl=%s) — retry next poll",
                ticket,
                gap,
                min_dist,
                price,
                raw_sl,
                lock_sl,
            )
            return trade

        if abs(raw_sl - lock_sl) >= 1e-6:
            log.info(
                "Lock SL adjusted ticket=%s raw=%s → adj=%s "
                "(price=%.2f min_dist=%.2f)",
                ticket,
                raw_sl,
                lock_sl,
                price,
                min_dist,
            )

        if dry_run:
            log.info(
                "DRY_RUN would set_sl ticket=%s → %s (repr=%r price=%.2f gap=%.2f min_dist=%.2f)",
                ticket,
                lock_sl,
                lock_sl,
                price,
                gap,
                min_dist,
            )
            payload = {"lock_state": "locked", "lock_sl": lock_sl, "stop": lock_sl}
            try:
                update_trade(trade_id, payload)
            except Exception:
                log.debug("lock columns missing on dry_run write", exc_info=True)
            return {**trade, **payload}

        log.info(
            "Lock set_sl attempt ticket=%s sl=%s repr=%r bid=%.2f ask=%.2f gap=%.2f min_dist=%.2f",
            ticket,
            lock_sl,
            lock_sl,
            bid,
            ask,
            gap,
            min_dist,
        )
        result = mt5.set_sl(int(ticket), lock_sl)
        if not result.ok:
            log.error(
                "set_sl failed ticket=%s retcode=%s comment=%r error=%s sent_sl=%r",
                ticket,
                result.retcode,
                result.comment,
                result.error,
                lock_sl,
            )
            try:
                update_trade(
                    trade_id,
                    {
                        "mt5_error": (
                            f"lock set_sl failed retcode={result.retcode} "
                            f"comment={result.comment!r} sent_sl={lock_sl!r} "
                            f"{result.error or ''}"
                        )[:500]
                    },
                )
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

        log.info(
            "Locked ticket=%s SL→%s (repr=%r) retcode=%s at start of bar %s",
            ticket,
            lock_sl,
            lock_sl,
            result.retcode,
            forming_t.isoformat(),
        )
        return {**trade, **payload}

    return trade


def resolve_exit_reason(
    trade: dict[str, Any],
    *,
    exit_price: float,
    mt5_reason: str,
) -> str:
    """Map MT5 close to journal exit_reason.

    Prefer price vs levels over broker reason codes (codes were historically
    mis-mapped; geometry is the source of truth for tp/sl/lock).
    """
    lock_state = (trade.get("lock_state") or "none").lower()
    lock_sl = _to_float(trade.get("lock_sl"))
    orig = _to_float(trade.get("orig_stop"))
    if orig is None:
        orig = _to_float(trade.get("stop"))
    tp = _to_float(trade.get("tp"))
    tol = 0.05

    if lock_state == "locked" and lock_sl is not None and abs(exit_price - lock_sl) <= tol:
        return "lock"

    if orig is not None and abs(exit_price - orig) <= tol:
        return "sl"

    if tp is not None and abs(exit_price - tp) <= tol:
        return "tp"

    if lock_state == "locked" and lock_sl is not None:
        # Locked but exit not exactly on lock_sl — still treat SL-side as lock.
        if mt5_reason in ("sl", "so") or (
            orig is not None and abs(exit_price - lock_sl) <= abs(exit_price - orig) + tol
        ):
            return "lock"

    if mt5_reason in ("tp", "sl", "lock"):
        return mt5_reason
    if mt5_reason == "so":
        return "sl"
    return mt5_reason