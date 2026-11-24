"""Pre-open gate: skip signals whose Pine path already finished (SL/TP/lock)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from lock import ENTRY_LOCK_DELAY, hit_lock_level, lock_sl_price
from mt5_client import MT5Client

log = logging.getLogger("xaubot.preopen")


@dataclass(frozen=True)
class PreOpenResult:
    result: str  # OK | OPEN_LOCKED | SKIP_*
    reason: str = ""
    lock_sl: float | None = None
    trade: dict[str, Any] | None = None


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
    return dt.astimezone(timezone.utc)


def _price_past_lock(direction: str, lock_sl: float, bid: float, ask: float) -> bool:
    if direction == "long":
        return bid <= lock_sl
    return ask >= lock_sl


def _hit_sl(direction: str, stop: float, high: float, low: float) -> bool:
    if direction == "long":
        return low <= stop
    return high >= stop


def _hit_tp(direction: str, tp: float, high: float, low: float) -> bool:
    if direction == "long":
        return high >= tp
    return low <= tp


def refresh_trade(fetch_by_id: Callable[[str], dict[str, Any] | None], trade_id: str) -> dict[str, Any] | None:
    return fetch_by_id(trade_id)


def preopen_check(
    trade: dict[str, Any],
    mt5: MT5Client,
    *,
    symbol: str,
    fetch_by_id: Callable[[str], dict[str, Any] | None],
) -> PreOpenResult:
    """Decide OK / OPEN_LOCKED / SKIP_* from a fresh journal row + M1 since entry+5m."""
    trade_id = str(trade.get("id") or "")
    fresh = refresh_trade(fetch_by_id, trade_id) if trade_id else None
    row = fresh or trade

    pine_status = (row.get("status") or "").strip().lower()
    direction = (row.get("direction") or "").strip().lower()
    entry = _to_float(row.get("entry"))
    tp = _to_float(row.get("tp"))
    stop = _to_float(row.get("orig_stop"))
    if stop is None:
        stop = _to_float(row.get("stop"))

    entry_t = _parse_ts(row.get("entry_time"))
    t0 = (entry_t + ENTRY_LOCK_DELAY) if entry_t is not None else None
    t0_srv = ""
    if t0 is not None:
        try:
            off = mt5.server_utc_offset(symbol)
            t0_srv = (t0 + off).isoformat()
        except Exception:
            t0_srv = t0.isoformat()

    bars: list[dict] = []
    bid = ask = 0.0
    hi = lo = None
    trig40 = lock30 = None
    n_bars = 0

    def _log(result: str) -> None:
        log.info(
            "PreOpen trade=%s result=%s t0_srv=%s n_bars=%s hi=%s lo=%s bid=%s ask=%s "
            "trig40=%s lock30=%s sl=%s tp=%s pine_status=%s",
            trade_id,
            result,
            t0_srv or "none",
            n_bars,
            hi if hi is not None else "none",
            lo if lo is not None else "none",
            bid,
            ask,
            trig40 if trig40 is not None else "none",
            lock30 if lock30 is not None else "none",
            stop if stop is not None else "none",
            tp if tp is not None else "none",
            pine_status or "none",
        )

    if pine_status != "open":
        _log("SKIP_PINE_CLOSED")
        return PreOpenResult(
            result="SKIP_PINE_CLOSED",
            reason="skip: pine already closed",
            trade=row,
        )

    if direction not in ("long", "short") or entry is None or tp is None or stop is None or t0 is None:
        _log("OK")
        return PreOpenResult(result="OK", trade=row)

    dist = abs(tp - entry)
    if direction == "long":
        trig40 = entry + 0.40 * dist
    else:
        trig40 = entry - 0.40 * dist
    lock30 = float(lock_sl_price(direction, entry, tp))

    try:
        bars = mt5.m1_bars_since(symbol, t0)
    except Exception:
        log.exception("PreOpen M1 fetch failed trade=%s", trade_id)
        bars = []
    n_bars = len(bars)

    age_t0 = (datetime.now(timezone.utc) - t0).total_seconds()
    if n_bars == 0 and age_t0 > 60:
        log.warning(
            "PreOpen trade=%s n_bars=0 after %.0fs from t0=%s — M1 window empty",
            trade_id,
            age_t0,
            t0.isoformat(),
        )

    if bars:
        hi = max(float(b["high"]) for b in bars)
        lo = min(float(b["low"]) for b in bars)

    try:
        tick = mt5.tick(symbol)
        bid = float(tick.bid)
        ask = float(tick.ask)
    except Exception:
        log.exception("PreOpen tick failed trade=%s", trade_id)

    any_sl = False
    any_tp = False
    any_lock = False
    for bar in bars:
        high = float(bar["high"])
        low = float(bar["low"])
        sl_here = _hit_sl(direction, stop, high, low)
        tp_here = _hit_tp(direction, tp, high, low)
        lock_here = hit_lock_level(direction, entry, tp, high, low)
        if sl_here and lock_here:
            any_sl = True
            any_lock = True
            break
        any_sl = any_sl or sl_here
        any_tp = any_tp or tp_here
        any_lock = any_lock or lock_here

    # Forming bar / live tick: treat bid/ask as a degenerate OHLC point.
    if bid > 0 and ask > 0:
        live_hi = max(bid, ask)
        live_lo = min(bid, ask)
        any_sl = any_sl or _hit_sl(direction, stop, live_hi, live_lo)
        any_tp = any_tp or _hit_tp(direction, tp, live_hi, live_lo)
        any_lock = any_lock or hit_lock_level(direction, entry, tp, live_hi, live_lo)

    if any_sl:
        _log("SKIP_SL_HIT")
        return PreOpenResult(result="SKIP_SL_HIT", reason="skip: sl already hit", trade=row)
    if any_tp:
        _log("SKIP_TP_HIT")
        return PreOpenResult(result="SKIP_TP_HIT", reason="skip: tp already hit", trade=row)
    if any_lock:
        if bid > 0 and ask > 0 and _price_past_lock(direction, lock30, bid, ask):
            _log("SKIP_LOCK_PASSED")
            return PreOpenResult(
                result="SKIP_LOCK_PASSED",
                reason="skip: lock already passed",
                lock_sl=lock30,
                trade=row,
            )
        _log("OPEN_LOCKED")
        return PreOpenResult(result="OPEN_LOCKED", lock_sl=lock30, trade=row)

    _log("OK")
    return PreOpenResult(result="OK", trade=row)
