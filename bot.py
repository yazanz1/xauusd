"""Copy wick_strategy (+60) signals from Supabase into MT5 (VPS / local).

Flow each poll:
1) open new signals (market + SL/TP)
2) manage 40/30 bot lock on open tickets (none → locked same poll, M1)
3) sync open tickets (floating / close → Supabase)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import Client, create_client

from bot1_filters import active_filters, run_filters
from gann import sync_gann_levels
from lock import manage_lock, resolve_exit_reason
from lot_mult import lot_mult
from mt5_client import MT5Client, MT5Error
from preopen import preopen_check
from sessions import SessionManager
from statics import sync_daily_statics

TABLE = "wick_strategy"
MAGIC = 260860
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
TRACK_DAYS = 7
DATE_TZ = ZoneInfo("Asia/Jerusalem")

# Official MetaTrader5 DEAL_REASON_* values (verified against package constants).
EXIT_REASONS = {
    0: "client",
    1: "mobile",
    2: "web",
    3: "expert",
    4: "sl",
    5: "tp",
    6: "so",
}

log = logging.getLogger("xaubot")


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing required env var: {name}")
    return value


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def connect_supabase() -> Client:
    return create_client(env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY"))


def fetch_new_signals(db: Client) -> list[dict[str, Any]]:
    """Open journal rows not yet claimed/skipped (copied_at still null)."""
    result = (
        db.table(TABLE)
        .select("*")
        .eq("status", "open")
        .is_("mt5_ticket", "null")
        .is_("copied_at", "null")
        .order("created_at")
        .execute()
    )
    return result.data or []


def fetch_trade_by_id(db: Client, trade_id: str) -> dict[str, Any] | None:
    result = db.table(TABLE).select("*").eq("id", trade_id).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def finalize_skip_signal(db: Client, trade: dict[str, Any], reason: str) -> None:
    """Mark signal handled without opening: copied_at set, ticket stays null.

    Does not overwrite an existing mt5_error that already explains another skip.
    """
    trade_id = trade["id"]
    now = iso(utcnow())
    payload: dict[str, Any] = {"copied_at": now, "updated_at": now}
    existing = (trade.get("mt5_error") or "").strip()
    if not existing:
        payload["mt5_error"] = reason[:500]
    db.table(TABLE).update(payload).eq("id", trade_id).is_("mt5_ticket", "null").execute()
    log.warning("Skip %s (final): %s", trade_id, reason)


def same_bar_open(db: Client, trade: dict[str, Any]) -> bool:
    """True if another row already opened MT5 for the same entry_time + direction."""
    entry_time = trade.get("entry_time")
    direction = (trade.get("direction") or "").strip().lower()
    trade_id = trade.get("id")
    if not entry_time or direction not in ("long", "short") or not trade_id:
        return False
    try:
        result = (
            db.table(TABLE)
            .select("id")
            .eq("entry_time", entry_time)
            .eq("direction", direction)
            .not_.is_("mt5_ticket", "null")
            .neq("id", trade_id)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception as exc:
        log.warning("same_bar_open failed: %s — treat as False", exc)
        return False


def fetch_open_copied(db: Client) -> list[dict[str, Any]]:
    """Rows the bot still owns: a ticket, no MT5 close yet, inside the last 7 Israel days.

    Webhook owns `status`. Do not filter on it — a journal close must not drop
    a live position (ticket 2057894665). Older rows without mt5_closed_at are
    left for resync, not the 5s loop.
    """
    cutoff = (datetime.now(DATE_TZ).date() - timedelta(days=TRACK_DAYS)).isoformat()
    result = (
        db.table(TABLE)
        .select("*")
        .gte("date_idt", cutoff)
        .not_.is_("mt5_ticket", "null")
        .is_("mt5_closed_at", "null")
        .execute()
    )
    return result.data or []


def update_trade(db: Client, trade_id: str, payload: dict[str, Any]) -> None:
    payload = {**payload, "updated_at": iso(utcnow())}
    db.table(TABLE).update(payload).eq("id", trade_id).execute()


def claim_signal(db: Client, trade_id: str) -> bool:
    """Optimistic lock: set copied_at only if still null (blocks double-open).

    On open failure the caller MUST clear copied_at again so the row stays
    eligible for fetch_new_signals retry.
    """
    now = iso(utcnow())
    result = (
        db.table(TABLE)
        .update({"copied_at": now, "updated_at": now, "mt5_error": None})
        .eq("id", trade_id)
        .is_("mt5_ticket", "null")
        .is_("copied_at", "null")
        .execute()
    )
    return bool(result.data)


def pip_size() -> float:
    return env_float("PIP_SIZE", 0.1)


def calc_pips(direction: str, entry: float | None, exit_price: float | None) -> float | None:
    if entry is None or exit_price is None:
        return None
    size = pip_size() or 0.1
    raw = (exit_price - entry) if direction == "long" else (entry - exit_price)
    return round(raw / size, 2)


def deal_exit_reason(reason: int) -> str:
    return EXIT_REASONS.get(reason, f"reason_{reason}")


def signal_age_ok(trade: dict[str, Any], max_age_sec: float) -> bool:
    if max_age_sec <= 0:
        return True
    created = parse_ts(trade.get("created_at"))
    if created is None:
        return True
    age = (utcnow() - created).total_seconds()
    return age <= max_age_sec


def slip_cap(signal_entry: float | None, stop: float | None, max_slip_usd: float, stop_frac: float) -> float:
    """Allowed slip: min(MAX_SLIP_USD, MAX_SLIP_STOP_FRAC × |entry − stop|).

    If entry/stop missing, fall back to the fixed USD cap only.
    """
    cap = max_slip_usd
    if signal_entry is not None and stop is not None and stop_frac > 0:
        relative = stop_frac * abs(signal_entry - stop)
        if relative > 0:
            cap = min(cap, relative)
    return cap


def slip_ok(
    mt5: MT5Client,
    symbol: str,
    side: str,
    signal_entry: float | None,
    max_slip: float,
) -> tuple[bool, float, float]:
    tick = mt5.tick(symbol)
    live = float(tick.ask if side == "buy" else tick.bid)
    if signal_entry is None or max_slip <= 0:
        return True, live, 0.0
    slip = abs(live - signal_entry)
    return slip <= max_slip, live, slip


def stops_distance_ok(mt5: MT5Client, symbol: str, side: str, price: float, sl: float, tp: float) -> bool:
    min_dist = mt5.min_stop_distance(symbol)
    if min_dist <= 0:
        return True
    if side == "buy":
        return (price - sl) >= min_dist and (tp - price) >= min_dist
    return (sl - price) >= min_dist and (price - tp) >= min_dist


def open_signal(db: Client, mt5: MT5Client, trade: dict[str, Any]) -> None:
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    lot_size = env_float("LOT_SIZE", 0.01)
    max_age = env_float("MAX_AGE_SEC", 180)
    max_slip_usd = env_float("MAX_SLIP_USD", 1.5)
    slip_stop_frac = env_float("MAX_SLIP_STOP_FRAC", 0.20)
    max_open = env_int("MAX_OPEN", 6)
    dry_run = env_bool("DRY_RUN", False)

    direction = (trade.get("direction") or "").lower()
    stop = to_float(trade.get("stop"))
    tp = to_float(trade.get("tp"))
    signal_entry = to_float(trade.get("entry"))
    trade_id = trade["id"]

    if direction not in ("long", "short"):
        finalize_skip_signal(db, trade, f"invalid direction: {direction}")
        return
    if stop is None or tp is None:
        finalize_skip_signal(db, trade, "missing stop or tp")
        return

    ctx = {
        "now": parse_ts(trade.get("created_at")) or utcnow(),
        "same_bar_open": same_bar_open(db, trade),
    }
    filt = run_filters(trade, active_filters(), context=ctx)
    if not filt.allowed:
        finalize_skip_signal(db, trade, filt.reason or "filter blocked")
        return

    if not signal_age_ok(trade, max_age):
        finalize_skip_signal(db, trade, f"signal too old (> {max_age}s)")
        return

    # Temporary deferrals: write mt5_error only — leave copied_at null so retry works.
    open_now = len(mt5.positions(symbol=symbol, magic=MAGIC))
    if open_now >= max_open:
        update_trade(db, trade_id, {"mt5_error": f"max open positions reached ({max_open})"})
        log.warning("Defer %s: max open %s (copied_at stays null)", trade_id, max_open)
        return

    side = "buy" if direction == "long" else "sell"
    max_slip = slip_cap(signal_entry, stop, max_slip_usd, slip_stop_frac)
    ok_slip, live, slip = slip_ok(mt5, symbol, side, signal_entry, max_slip)
    if not ok_slip:
        update_trade(
            db,
            trade_id,
            {
                "mt5_error": (
                    f"slip {slip:.2f} > max {max_slip:.2f} "
                    f"(cap=min({max_slip_usd:.2f}, {slip_stop_frac:.0%}×|entry-stop|); "
                    f"live={live}, signal={signal_entry})"
                )
            },
        )
        log.warning("Defer %s: slip %.2f > %.2f (copied_at stays null)", trade_id, slip, max_slip)
        return

    if not stops_distance_ok(mt5, symbol, side, live, stop, tp):
        update_trade(db, trade_id, {"mt5_error": "SL/TP too close for broker stops level"})
        log.warning("Defer %s: stops too close (copied_at stays null)", trade_id)
        return

    open_locked = False
    open_sl = stop
    preopen_enabled = env_bool("PREOPEN_CHECK", True)
    preopen_mode = (os.getenv("PREOPEN_MODE") or "block").strip().lower()
    if preopen_mode not in ("block", "shadow"):
        preopen_mode = "block"

    if preopen_enabled:
        pre = preopen_check(
            trade,
            mt5,
            symbol=symbol,
            fetch_by_id=lambda tid, _db=db: fetch_trade_by_id(_db, tid),
        )
        if pre.trade is not None:
            trade = pre.trade
            stop = to_float(trade.get("orig_stop")) or to_float(trade.get("stop")) or stop
            tp = to_float(trade.get("tp")) or tp
            signal_entry = to_float(trade.get("entry")) or signal_entry
            direction = (trade.get("direction") or direction).lower()
            side = "buy" if direction == "long" else "sell"
            open_sl = stop

        if pre.result.startswith("SKIP_"):
            if preopen_mode == "shadow":
                log.info(
                    "PreOpen shadow would %s for %s — opening anyway",
                    pre.result,
                    trade_id,
                )
            else:
                finalize_skip_signal(db, trade, pre.reason or pre.result)
                return
        elif pre.result == "OPEN_LOCKED" and preopen_mode == "block":
            lock_sl = pre.lock_sl
            if lock_sl is None:
                log.info("PreOpen OPEN_LOCKED %s without lock_sl — open with journal stop", trade_id)
            else:
                lock_sl = float(mt5.normalize_price(lock_sl, symbol))
                # Re-check live vs lock30: may have crossed since preopen M1 scan.
                tick_now = mt5.tick(symbol)
                bid_now, ask_now = float(tick_now.bid), float(tick_now.ask)
                if direction == "long" and bid_now <= lock_sl:
                    finalize_skip_signal(db, trade, "skip: lock already passed")
                    return
                if direction == "short" and ask_now >= lock_sl:
                    finalize_skip_signal(db, trade, "skip: lock already passed")
                    return
                # Distance for placing SL uses fill side (ask for buy / bid for sell).
                price_now = ask_now if direction == "long" else bid_now
                min_dist = float(mt5.min_stop_distance(symbol))
                gap = (price_now - lock_sl) if direction == "long" else (lock_sl - price_now)
                if min_dist > 0 and gap < min_dist:
                    # Broker would reject lock30 SL — open with orig stop; manage_lock next poll.
                    open_locked = False
                    open_sl = stop
                    log.info(
                        "PreOpen OPEN_LOCKED %s lock30=%s gap=%.2f < min_dist=%.2f "
                        "— open with orig stop; manage_lock next poll",
                        trade_id,
                        lock_sl,
                        gap,
                        min_dist,
                    )
                else:
                    open_locked = True
                    open_sl = lock_sl
                    log.info(
                        "PreOpen OPEN_LOCKED %s — open with SL=%s (lock 30%%)",
                        trade_id,
                        open_sl,
                    )
        elif pre.result == "OPEN_LOCKED" and preopen_mode == "shadow":
            log.info(
                "PreOpen shadow would OPEN_LOCKED for %s (lock_sl=%s) — opening with journal stop",
                trade_id,
                pre.lock_sl,
            )

    mult = lot_mult(trade.get("lot_mult"))
    volume = mt5.normalize_volume(lot_size * mult, symbol)
    log.info(
        "Open %s lot=%s (LOT_SIZE=%s × lot_mult=%s)",
        trade_id,
        volume,
        lot_size,
        mult,
    )

    if dry_run:
        update_trade(
            db,
            trade_id,
            {
                "mt5_error": (
                    f"DRY_RUN would {side} {symbol} lots={volume} live={live} "
                    f"sl={open_sl} tp={tp} slip={slip:.2f}"
                    + (" open_locked=1" if open_locked else "")
                )
            },
        )
        log.info(
            "DRY_RUN %s %s live=%s sl=%s tp=%s slip=%.2f open_locked=%s",
            side,
            symbol,
            live,
            open_sl,
            tp,
            slip,
            open_locked,
        )
        return

    if not claim_signal(db, trade_id):
        log.info("Skip %s: already claimed by another worker", trade_id)
        return

    result = mt5.open_market(
        symbol,
        side,
        volume,
        sl=open_sl,
        tp=tp,
        comment=f"wick:{trade_id[:8]}",
        magic=MAGIC,
    )
    if not result.ok:
        err = (result.error or result.comment)[:500]
        # Release claim so fetch_new_signals can retry (copied_at must stay null).
        update_trade(db, trade_id, {"mt5_error": err, "copied_at": None})
        log.error("Open failed %s — released claim: %s", trade_id, err)
        return

    ticket = int(result.ticket or 0)
    fill_price = result.price
    if fill_price is None:
        tick = mt5.tick(symbol)
        fill_price = tick.ask if side == "buy" else tick.bid
    fill_price = mt5.normalize_price(fill_price, symbol)

    payload = {
        "mt5_ticket": ticket,
        "copied_at": iso(utcnow()),
        "mt5_error": None,
        "mt5_fill_price": fill_price,
        "stop": result.sl if result.sl is not None else open_sl,
        "tp": result.tp if result.tp is not None else tp,
        "bot_lock_state": "locked" if open_locked else "none",
        "bot_lock_sl": (result.sl if result.sl is not None else open_sl) if open_locked else None,
        "bot_lock_pend_bar_time": None,
        "orig_stop": stop,
    }
    try:
        update_trade(db, trade_id, payload)
    except Exception:
        # Columns may be missing until SQL migrations run.
        for key in (
            "mt5_fill_price",
            "bot_lock_state",
            "bot_lock_sl",
            "bot_lock_pend_bar_time",
            "orig_stop",
        ):
            payload.pop(key, None)
        update_trade(db, trade_id, payload)
        log.warning("optional columns missing — apply sql/wick_bot_lock_columns.sql")

    log.info(
        "Opened %s %s ticket=%s volume=%s fill=%s sl=%s tp=%s open_locked=%s",
        direction,
        symbol,
        ticket,
        result.volume,
        fill_price,
        result.sl,
        result.tp,
        open_locked,
    )


def history_deals_for(mt5: MT5Client, ticket: int, _copied_at: Any = None):
    """Deals belonging to this position ticket only (no time-window / order-id mixups)."""
    return mt5.history_deals_by_position(int(ticket))


def check_orphans(db: Client, mt5: MT5Client) -> None:
    """Log MT5 positions (our magic) that have no status=open row — do not auto-close."""
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    tracked = {
        int(r["mt5_ticket"])
        for r in fetch_open_copied(db)
        if r.get("mt5_ticket") is not None
    }
    for pos in mt5.positions(symbol=symbol, magic=MAGIC):
        ticket = int(pos.ticket)
        if ticket not in tracked:
            log.error(
                "ORPHAN: position %s open in MT5 but not in the bot track "
                "(no mt5_ticket row with mt5_closed_at null in the last %s days)",
                ticket,
                TRACK_DAYS,
            )


def _pine_closed_ago_sec(trade: dict[str, Any]) -> float | None:
    """Seconds since webhook wrote closed status (updated_at only)."""
    if (trade.get("status") or "").strip().lower() != "closed":
        return None
    when = parse_ts(trade.get("updated_at"))
    if when is None:
        return None
    return max(0.0, (utcnow() - when).total_seconds())


def _opened_ago_sec(trade: dict[str, Any], pos: Any = None) -> float | None:
    """Seconds since MT5 open (copied_at, else position time)."""
    when = parse_ts(trade.get("copied_at"))
    if when is None and pos is not None:
        try:
            when = datetime.fromtimestamp(int(pos.time), tz=timezone.utc)
        except Exception:
            when = None
    if when is None:
        return None
    return max(0.0, (utcnow() - when).total_seconds())


def sync_trade(db: Client, mt5: MT5Client, trade: dict[str, Any]) -> None:
    ticket = int(trade["mt5_ticket"])
    pos = mt5.get_position(ticket)
    dry_run = env_bool("DRY_RUN", False)
    pine_exit_sec = env_float("PINE_EXIT_SYNC_SEC", 3.0)
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")

    if pos is not None:
        # Pine already closed the journal but MT5 position still lives → sync close.
        pine_ago = _pine_closed_ago_sec(trade)
        opened_ago = _opened_ago_sec(trade, pos)
        if (
            pine_ago is not None
            and pine_ago > pine_exit_sec
            and (opened_ago is None or opened_ago >= pine_exit_sec)
        ):
            try:
                tick = mt5.tick(symbol)
                bid, ask = float(tick.bid), float(tick.ask)
            except Exception:
                bid = ask = 0.0
            log.info(
                "PineExitSync ticket=%s pine_exit=%s pine_reason=%s wait=%.1f "
                "bid=%s ask=%s broker_sl=%s broker_tp=%s",
                ticket,
                trade.get("exit"),
                trade.get("exit_reason"),
                pine_ago,
                bid,
                ask,
                getattr(pos, "sl", None),
                getattr(pos, "tp", None),
            )
            if dry_run:
                log.info("DRY_RUN would PINE_EXIT_SYNC close ticket=%s", ticket)
                floating = round(float(pos.profit + pos.swap), 2)
                update_trade(db, trade["id"], {"mt5_profit": floating})
                return

            result = mt5.safe_close_position(ticket)
            if not result.ok:
                log.error(
                    "PINE_EXIT_SYNC close failed ticket=%s retcode=%s comment=%r error=%s",
                    ticket,
                    result.retcode,
                    result.comment,
                    result.error,
                )
                try:
                    update_trade(
                        db,
                        trade["id"],
                        {
                            "mt5_error": (
                                f"PINE_EXIT_SYNC close failed retcode={result.retcode} "
                                f"{result.error or result.comment or ''}"
                            )[:500]
                        },
                    )
                except Exception:
                    pass
                return

            # already_closed (missing / 10036) or successful send → history sync below.
            pos = mt5.get_position(ticket)
            if pos is not None and (result.error or "") != "already_closed":
                log.error("PINE_EXIT_SYNC ticket=%s still open after close_position", ticket)
                return
            # Fall through to OUT-deal sync below.
        else:
            floating = round(float(pos.profit + pos.swap), 2)
            prev_profit = to_float(trade.get("mt5_profit"))
            profit_changed = prev_profit is None or abs(prev_profit - floating) >= 0.01

            if not profit_changed:
                log.debug(
                    "Open ticket=%s unchanged floating=%s bot_lock=%s",
                    ticket,
                    floating,
                    trade.get("bot_lock_state") or "none",
                )
                return

            update_trade(db, trade["id"], {"mt5_profit": floating})
            log.info(
                "Open ticket=%s floating=%s bot_lock=%s",
                ticket,
                floating,
                trade.get("bot_lock_state") or "none",
            )
            return

    # No open position — only mark closed if we have an OUT deal for THIS position.
    deals = history_deals_for(mt5, ticket, trade.get("copied_at"))
    if any(int(d.position_id) != ticket for d in deals):
        log.error(
            "Ticket %s: refusing close — deal position_id mismatch in history",
            ticket,
        )
        return
    out_deals = [d for d in deals if d.entry in (DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT)]
    if not out_deals:
        log.warning(
            "Ticket %s: no position and no exit deal for this position_id — not marking closed",
            ticket,
        )
        return

    last = max(out_deals, key=lambda d: d.time)
    if int(last.position_id) != ticket:
        log.error("Ticket %s: exit deal position_id=%s — refusing close", ticket, last.position_id)
        return
    profit = round(
        sum(d.profit + d.swap + getattr(d, "commission", 0.0) for d in deals),
        2,
    )
    exit_price = float(last.price)
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    exit_dt = mt5.deal_time_to_utc(int(last.time), symbol)
    mt5_reason = deal_exit_reason(int(last.reason))
    exit_reason = resolve_exit_reason(trade, exit_price=exit_price, mt5_reason=mt5_reason)
    payload = {
        "mt5_close_price": exit_price,
        "mt5_closed_at": iso(exit_dt),
        "mt5_profit": profit,
        "mt5_exit_reason": exit_reason,
    }
    update_trade(db, trade["id"], payload)

    log.info(
        "MT5 close ticket=%s mt5_exit_reason=%s exit=%s profit=%s closed_at=%s",
        ticket,
        exit_reason,
        exit_price,
        profit,
        payload["mt5_closed_at"],
    )


def run_cycle(db: Client, mt5: MT5Client) -> None:
    if not mt5.ensure_connected():
        log.error("Skipping cycle: MT5 is not connected")
        return

    try:
        sync_gann_levels(db, mt5)
    except Exception:
        log.exception("Gann level sync failed")

    try:
        sync_daily_statics(db)
    except Exception:
        log.exception("Daily statics sync failed")

    new_signals = fetch_new_signals(db)
    log.info("New signals: %s", len(new_signals))
    for trade in new_signals:
        try:
            open_signal(db, mt5, trade)
        except Exception:
            log.exception("Failed to open trade %s", trade.get("id"))
            try:
                update_trade(db, trade["id"], {"mt5_error": "exception while opening, see bot log"})
            except Exception:
                log.exception("Could not write mt5_error for %s", trade.get("id"))

    open_copied = fetch_open_copied(db)
    log.info("Open copied trades: %s", len(open_copied))
    dry_run = env_bool("DRY_RUN", False)
    for trade in open_copied:
        try:
            trade = manage_lock(
                trade,
                mt5,
                update_trade=lambda tid, payload, _db=db: update_trade(_db, tid, payload),
                dry_run=dry_run,
            )
            sync_trade(db, mt5, trade)
        except Exception:
            log.exception("Failed to sync/lock trade %s", trade.get("id"))

    try:
        check_orphans(db, mt5)
    except Exception:
        log.exception("Orphan position check failed")


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    poll_sec = env_float("POLL_SEC", 5)
    dry_run = env_bool("DRY_RUN", False)

    db = connect_supabase()
    session = SessionManager(magic=MAGIC)
    try:
        mt5 = session.open()
    except MT5Error as exc:
        raise SystemExit(str(exc)) from exc

    account = mt5.account()
    log.info("MT5 connected: login=%s server=%s", account.login, account.server)
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    try:
        info = mt5.symbol_info(symbol)
        stops_pts = int(getattr(info, "trade_stops_level", 0) or 0)
        stops_usd = float(mt5.min_stop_distance(symbol))
        log.info(
            "Symbol %s stops_level=%s points = $%.2f (lock floor uses ×1.2)",
            symbol,
            stops_pts,
            stops_usd,
        )
    except Exception:
        log.exception("Could not read stops_level for %s", symbol)
    log.info("wick table=%s magic=%s filters=[]", TABLE, MAGIC)
    log.info(
        "Bot started. poll=%ss dry_run=%s lot=%s symbol=%s",
        poll_sec,
        dry_run,
        env_float("LOT_SIZE", 0.01),
        symbol,
    )

    try:
        while True:
            log.info("Scan started")
            try:
                mt5 = session.ensure()
            except MT5Error as exc:
                log.error("MT5 session lost: %s", exc)
                time.sleep(poll_sec)
                continue
            run_cycle(db, mt5)
            # Local heartbeat for watchdog: fresh only after a completed cycle.
            (Path(__file__).resolve().parent / "heartbeat.txt").write_text(
                iso(utcnow()), encoding="utf-8"
            )
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        session.close()


if __name__ == "__main__":
    main()
