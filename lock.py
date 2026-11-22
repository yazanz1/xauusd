"""40/30 lock manager — bot-side lock for wick_strategy (+60).

States: none → locked (same poll; no next-bar wait)

- Formula uses webhook `entry` + `tp` (not mt5_fill_price).
- Detect 40% hit on M1 high/low from entry_time + 5 minutes (bar after entry).
- On hit, set_sl to 30% in the same poll. Persist only `bot_lock_*` (never webhook lock_* / stop).
- If price already past the 30% lock SL (wrong side for broker), market-close as lock.
- Webhook may write lock_state=locked independently; manage_lock ignores those columns.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mt5_client import MT5Client

log = logging.getLogger("xaubot.lock")

LOCK_HIT_FRAC = 0.4
# Pine checks locks starting on the bar after entry (5m close → +300s).
ENTRY_LOCK_DELAY = timedelta(seconds=300)


def _lock_sl_frac() -> float:
    raw = os.getenv("LOCK_SL_FRAC", "0.30").strip()
    try:
        return float(raw)
    except ValueError:
        return 0.30


# Module-level default for tests / imports; runtime uses _lock_sl_frac().
LOCK_SL_FRAC = _lock_sl_frac()


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def lock_sl_price(direction: str, entry: float, tp: float) -> float:
    dist = abs(tp - entry)
    stop_frac = _lock_sl_frac()
    if direction == "long":
        return entry + stop_frac * dist
    return entry - stop_frac * dist


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
    """Pull SL away from the market; floor/cap at fill only when fill is still valid.

    Call only when lock_sl is already on the profit side of price.
    If fill slipped past lock_sl (long fill > lock, short fill < lock), do **not**
    clamp to fill — that would shove SL onto the wrong side of market. Keep the
    gap-adjusted lock_sl; caller market-closes only if price is already past it.
    """
    gap = max(min_dist * 1.2, 0.0)
    if direction == "long":
        adjusted = lock_sl if gap <= 0 else min(lock_sl, price - gap)
        if fill_price > lock_sl:
            return adjusted
        return max(adjusted, fill_price)
    adjusted = lock_sl if gap <= 0 else max(lock_sl, price + gap)
    if fill_price < lock_sl:
        return adjusted
    return min(adjusted, fill_price)


# Back-compat alias
hit_50 = hit_lock_level


def _lock_scan_start(trade: dict[str, Any]) -> datetime:
    """M1 window starts at entry_time + 5m (Pine: bar after entry close).

    Fallback only if entry_time is missing: copied_at, then last 6h.
    """
    entry_t = _parse_ts(trade.get("entry_time"))
    if entry_t is not None:
        return entry_t + ENTRY_LOCK_DELAY
    log.warning(
        "lock scan: missing entry_time on trade %s — fallback to copied_at/6h",
        trade.get("id"),
    )
    copied = _parse_ts(trade.get("copied_at"))
    if copied is not None:
        return copied
    return datetime.now(timezone.utc) - timedelta(hours=6)


# One diagnostic log per trade id for M1 window alignment (UTC).
_m1_scan_logged: set[str] = set()


def _m1_hit_40(
    mt5: MT5Client,
    symbol: str,
    direction: str,
    entry: float,
    tp: float,
    trade: dict[str, Any],
) -> bool:
    since = _lock_scan_start(trade)
    bars = mt5.m1_bars_since(symbol, since)
    trade_id = str(trade.get("id") or "")
    if trade_id and trade_id not in _m1_scan_logged:
        _m1_scan_logged.add(trade_id)
        if bars:
            first_utc = datetime.fromtimestamp(int(bars[0]["time"]), tz=timezone.utc)
            last_utc = datetime.fromtimestamp(int(bars[-1]["time"]), tz=timezone.utc)
            log.info(
                "M1 lock scan trade=%s scan_from_utc=%s first_bar_utc=%s last_bar_utc=%s n=%s",
                trade_id,
                since.isoformat(),
                first_utc.isoformat(),
                last_utc.isoformat(),
                len(bars),
            )
        else:
            log.info(
                "M1 lock scan trade=%s scan_from_utc=%s first_bar_utc=none last_bar_utc=none n=0",
                trade_id,
                since.isoformat(),
            )
    if not bars:
        return False
    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])
        if hit_lock_level(direction, entry, tp, high, low):
            return True
    return False


def _price_past_lock_sl(direction: str, lock_sl: float, bid: float, ask: float) -> bool:
    """True when market is already on/through the lock SL (cannot place SL)."""
    if direction == "long":
        return bid <= lock_sl
    return ask >= lock_sl


def _market_close_as_lock(
    trade: dict[str, Any],
    mt5: MT5Client,
    *,
    update_trade: Callable[[str, dict[str, Any]], None],
    dry_run: bool,
    ticket: int,
    trade_id: str,
    lock_sl: float,
    reason: str,
) -> dict[str, Any]:
    if dry_run:
        log.info(
            "DRY_RUN would market-close ticket=%s as lock (sl=%s reason=%s)",
            ticket,
            lock_sl,
            reason,
        )
        payload = {
            "bot_lock_state": "locked",
            "bot_lock_sl": lock_sl,
            "mt5_exit_reason": "lock",
        }
        try:
            update_trade(trade_id, payload)
        except Exception:
            log.debug("bot_lock columns missing on dry_run close", exc_info=True)
        return {**trade, **payload}

    log.warning(
        "Lock past price ticket=%s — market close as lock (sl=%s reason=%s)",
        ticket,
        lock_sl,
        reason,
    )
    result = mt5.close_position(int(ticket))
    payload: dict[str, Any] = {
        "bot_lock_state": "locked",
        "bot_lock_sl": lock_sl,
        "mt5_exit_reason": "lock",
    }
    if not result.ok:
        log.error(
            "market close failed ticket=%s retcode=%s comment=%r error=%s",
            ticket,
            result.retcode,
            result.comment,
            result.error,
        )
        payload["mt5_error"] = (
            f"lock market close failed retcode={result.retcode} "
            f"comment={result.comment!r} {result.error or ''}"
        )[:500]
        try:
            update_trade(trade_id, payload)
        except Exception:
            pass
        return {**trade, **payload}

    payload["mt5_error"] = None
    if result.price is not None:
        payload["mt5_close_price"] = result.price
    try:
        update_trade(trade_id, payload)
    except Exception:
        log.exception("Could not write lock market-close for %s", trade_id)
        return trade

    log.info(
        "Locked via market close ticket=%s exit=%s (strategy lock when SL already past)",
        ticket,
        result.price,
    )
    return {**trade, **payload}


def _try_set_lock_sl(
    trade: dict[str, Any],
    mt5: MT5Client,
    *,
    update_trade: Callable[[str, dict[str, Any]], None],
    dry_run: bool,
    direction: str,
    entry: float,
    tp: float,
    ticket: int,
    symbol: str,
    trade_id: str,
) -> dict[str, Any]:
    tick = mt5.tick(symbol)
    bid = float(tick.bid)
    ask = float(tick.ask)
    price = bid if direction == "long" else ask
    min_dist = float(mt5.min_stop_distance(symbol))
    raw_sl = float(mt5.normalize_price(lock_sl_price(direction, entry, tp), symbol))
    fill = _to_float(trade.get("mt5_fill_price"))
    if fill is None:
        log.info(
            "Skip lock set_sl ticket=%s: missing mt5_fill_price — retry next poll",
            ticket,
        )
        return trade

    # 40% already hit; price through 30% → market close as lock (Pine behavior).
    if _price_past_lock_sl(direction, raw_sl, bid, ask):
        return _market_close_as_lock(
            trade,
            mt5,
            update_trade=update_trade,
            dry_run=dry_run,
            ticket=ticket,
            trade_id=trade_id,
            lock_sl=raw_sl,
            reason="bid/ask past lock_sl before set_sl",
        )

    lock_sl = clamp_lock_sl_to_broker(
        direction, raw_sl, price=price, min_dist=min_dist, fill_price=fill
    )
    lock_sl = mt5.normalize_price(lock_sl, symbol)
    gap = abs(price - lock_sl)

    if direction == "long" and not (lock_sl < bid):
        return _market_close_as_lock(
            trade,
            mt5,
            update_trade=update_trade,
            dry_run=dry_run,
            ticket=ticket,
            trade_id=trade_id,
            lock_sl=raw_sl,
            reason="clamped long SL not below bid",
        )
    if direction == "short" and not (lock_sl > ask):
        return _market_close_as_lock(
            trade,
            mt5,
            update_trade=update_trade,
            dry_run=dry_run,
            ticket=ticket,
            trade_id=trade_id,
            lock_sl=raw_sl,
            reason="clamped short SL not above ask",
        )

    if min_dist > 0 and gap < min_dist:
        # Too tight to broker — if also on wrong side of intended lock, close; else retry.
        if _price_past_lock_sl(direction, raw_sl, bid, ask):
            return _market_close_as_lock(
                trade,
                mt5,
                update_trade=update_trade,
                dry_run=dry_run,
                ticket=ticket,
                trade_id=trade_id,
                lock_sl=raw_sl,
                reason=f"gap {gap:.2f} < min_dist {min_dist:.2f} and past lock",
            )
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
            "Lock SL adjusted ticket=%s raw=%s → adj=%s (price=%.2f min_dist=%.2f)",
            ticket,
            raw_sl,
            lock_sl,
            price,
            min_dist,
        )

    if dry_run:
        log.info(
            "DRY_RUN would set_sl ticket=%s → %s (price=%.2f gap=%.2f min_dist=%.2f)",
            ticket,
            lock_sl,
            price,
            gap,
            min_dist,
        )
        payload = {"bot_lock_state": "locked", "bot_lock_sl": lock_sl}
        try:
            update_trade(trade_id, payload)
        except Exception:
            log.debug("bot_lock columns missing on dry_run write", exc_info=True)
        return {**trade, **payload}

    log.info(
        "Lock set_sl attempt ticket=%s sl=%s bid=%.2f ask=%.2f gap=%.2f min_dist=%.2f",
        ticket,
        lock_sl,
        bid,
        ask,
        gap,
        min_dist,
    )
    result = mt5.set_sl(int(ticket), lock_sl)
    if not result.ok:
        # Broker rejected — if price already through lock, close as lock.
        tick2 = mt5.tick(symbol)
        bid2, ask2 = float(tick2.bid), float(tick2.ask)
        if _price_past_lock_sl(direction, raw_sl, bid2, ask2):
            return _market_close_as_lock(
                trade,
                mt5,
                update_trade=update_trade,
                dry_run=dry_run,
                ticket=ticket,
                trade_id=trade_id,
                lock_sl=raw_sl,
                reason=f"set_sl rejected retcode={result.retcode}",
            )
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
        "bot_lock_state": "locked",
        "bot_lock_sl": result.sl if result.sl is not None else lock_sl,
        "mt5_error": None,
    }
    try:
        update_trade(trade_id, payload)
    except Exception:
        log.exception("Could not write bot_lock locked for %s", trade_id)
        return trade

    log.info(
        "Locked ticket=%s SL→%s retcode=%s (40%% M1 hit → 30%% same poll)",
        ticket,
        payload["bot_lock_sl"],
        result.retcode,
    )
    return {**trade, **payload}


def manage_lock(
    trade: dict[str, Any],
    mt5: MT5Client,
    *,
    update_trade: Callable[[str, dict[str, Any]], None],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Advance bot lock for one open copied trade. Returns updated local fields."""
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

    # Bot-only state — ignore webhook lock_state.
    state = (trade.get("bot_lock_state") or "none").lower()
    if state == "locked":
        return trade

    pos = mt5.get_position(int(ticket))
    if pos is None:
        return trade

    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    trade_id = trade["id"]

    # Prefer orig_stop only — webhook may overwrite `stop` with lock_sl.
    orig_stop = _to_float(trade.get("orig_stop"))
    if orig_stop is None:
        # First time only: capture from open SL on the position, not journal stop.
        pos_sl = float(pos.sl or 0) or None
        if pos_sl is not None:
            try:
                update_trade(trade_id, {"orig_stop": pos_sl})
                trade = {**trade, "orig_stop": pos_sl}
            except Exception:
                log.debug("orig_stop column missing", exc_info=True)

    if state != "none":
        state = "none"

    if not _m1_hit_40(mt5, symbol, direction, entry, tp, trade):
        return trade

    log.info(
        "LockHit ticket=%s 40%% on M1 since entry_time+5m — set_sl same poll (LOCK_SL_FRAC=%s)",
        ticket,
        _lock_sl_frac(),
    )
    return _try_set_lock_sl(
        trade,
        mt5,
        update_trade=update_trade,
        dry_run=dry_run,
        direction=direction,
        entry=entry,
        tp=tp,
        ticket=int(ticket),
        symbol=symbol,
        trade_id=trade_id,
    )


def resolve_exit_reason(
    trade: dict[str, Any],
    *,
    exit_price: float,
    mt5_reason: str,
) -> str:
    """Map MT5 close to mt5_exit_reason using bot_lock_* geometry."""
    # Already stamped by market-close-as-lock path.
    existing = (trade.get("mt5_exit_reason") or "").strip().lower()
    if existing == "lock":
        return "lock"

    lock_state = (trade.get("bot_lock_state") or "none").lower()
    lock_sl = _to_float(trade.get("bot_lock_sl"))
    orig = _to_float(trade.get("orig_stop"))
    # Do not fall back to journal `stop` — webhook may have overwritten it with lock_sl.
    tp = _to_float(trade.get("tp"))
    tol = 0.05

    if lock_state == "locked" and lock_sl is not None and abs(exit_price - lock_sl) <= tol:
        return "lock"

    if orig is not None and abs(exit_price - orig) <= tol:
        return "sl"

    if tp is not None and abs(exit_price - tp) <= tol:
        return "tp"

    if lock_state == "locked" and lock_sl is not None:
        # Locked + broker/bot close (incl. market close as lock).
        if mt5_reason in ("sl", "so", "expert", "client"):
            return "lock"
        if orig is not None and abs(exit_price - lock_sl) <= abs(exit_price - orig) + tol:
            return "lock"

    if mt5_reason in ("tp", "sl", "lock"):
        return mt5_reason
    if mt5_reason == "so":
        return "sl"
    return mt5_reason
