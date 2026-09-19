"""Copy gold_trades signals from Supabase into MT5 (VPS / local).

Flow each poll:
1) open new signals (market + SL/TP)
2) manage 40/35 lock on open tickets (none → pend → locked)
3) sync open tickets (floating / close → Supabase)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from filters import active_filters, run_filters
from gann import sync_gann_levels
from lock import manage_lock, resolve_exit_reason
from mt5_client import MT5Client, MT5Error
from sessions import SessionManager
from statics import sync_daily_statics

TABLE = "gold_trades"
MAGIC = 260831
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2

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
    result = (
        db.table(TABLE)
        .select("*")
        .eq("status", "open")
        .is_("mt5_ticket", "null")
        .order("created_at")
        .execute()
    )
    return result.data or []


def fetch_open_copied(db: Client) -> list[dict[str, Any]]:
    result = (
        db.table(TABLE)
        .select("*")
        .eq("status", "open")
        .not_.is_("mt5_ticket", "null")
        .execute()
    )
    return result.data or []


def update_trade(db: Client, trade_id: str, payload: dict[str, Any]) -> None:
    payload = {**payload, "updated_at": iso(utcnow())}
    db.table(TABLE).update(payload).eq("id", trade_id).execute()


def claim_signal(db: Client, trade_id: str) -> bool:
    """Mark copied_at only if still null — prevents double-open by two workers."""
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


def slip_ok(mt5: MT5Client, symbol: str, side: str, signal_entry: float | None, max_slip: float) -> tuple[bool, float, float]:
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
    lots = env_float("LOT_SIZE", 0.01)
    max_age = env_float("MAX_AGE_SEC", 180)
    max_slip = env_float("MAX_SLIP_USD", 1.5)
    max_open = env_int("MAX_OPEN", 2)
    dry_run = env_bool("DRY_RUN", False)

    direction = (trade.get("direction") or "").lower()
    stop = to_float(trade.get("stop"))
    tp = to_float(trade.get("tp"))
    signal_entry = to_float(trade.get("entry"))
    trade_id = trade["id"]

    if direction not in ("long", "short"):
        update_trade(db, trade_id, {"mt5_error": f"invalid direction: {direction}"})
        log.error("Skip %s: invalid direction %s", trade_id, direction)
        return
    if stop is None or tp is None:
        update_trade(db, trade_id, {"mt5_error": "missing stop or tp"})
        log.error("Skip %s: missing stop/tp", trade_id)
        return

    filt = run_filters(trade, active_filters())
    if not filt.allowed:
        update_trade(db, trade_id, {"mt5_error": filt.reason})
        log.warning("Skip %s: %s", trade_id, filt.reason)
        return

    if not signal_age_ok(trade, max_age):
        update_trade(db, trade_id, {"mt5_error": f"signal too old (> {max_age}s)"})
        log.warning("Skip %s: signal too old", trade_id)
        return

    open_now = len(mt5.positions(symbol=symbol, magic=MAGIC))
    if open_now >= max_open:
        update_trade(db, trade_id, {"mt5_error": f"max open positions reached ({max_open})"})
        log.warning("Skip %s: max open %s", trade_id, max_open)
        return

    side = "buy" if direction == "long" else "sell"
    ok_slip, live, slip = slip_ok(mt5, symbol, side, signal_entry, max_slip)
    if not ok_slip:
        update_trade(
            db,
            trade_id,
            {"mt5_error": f"slip {slip:.2f} > max {max_slip} (live={live}, signal={signal_entry})"},
        )
        log.warning("Skip %s: slip %.2f > %.2f", trade_id, slip, max_slip)
        return

    if not stops_distance_ok(mt5, symbol, side, live, stop, tp):
        update_trade(db, trade_id, {"mt5_error": "SL/TP too close for broker stops level"})
        log.warning("Skip %s: stops too close", trade_id)
        return

    if dry_run:
        update_trade(
            db,
            trade_id,
            {
                "mt5_error": (
                    f"DRY_RUN would {side} {symbol} lots={lots} live={live} "
                    f"sl={stop} tp={tp} slip={slip:.2f}"
                )
            },
        )
        log.info(
            "DRY_RUN %s %s live=%s sl=%s tp=%s slip=%.2f",
            side,
            symbol,
            live,
            stop,
            tp,
            slip,
        )
        return

    if not claim_signal(db, trade_id):
        log.info("Skip %s: already claimed by another worker", trade_id)
        return

    result = mt5.open_market(
        symbol,
        side,
        lots,
        sl=stop,
        tp=tp,
        comment=f"gold:{trade_id[:8]}",
        magic=MAGIC,
    )
    if not result.ok:
        err = (result.error or result.comment)[:500]
        update_trade(db, trade_id, {"mt5_error": err, "copied_at": None})
        log.error("Open failed %s: %s", trade_id, err)
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
        "stop": result.sl if result.sl is not None else stop,
        "tp": result.tp if result.tp is not None else tp,
        "lock_state": "none",
        "lock_sl": None,
        "lock_pend_bar_time": None,
        "orig_stop": stop,
    }
    try:
        update_trade(db, trade_id, payload)
    except Exception:
        # Columns may be missing until SQL migrations run.
        for key in ("mt5_fill_price", "lock_state", "lock_sl", "lock_pend_bar_time", "orig_stop"):
            payload.pop(key, None)
        update_trade(db, trade_id, payload)
        log.warning("optional columns missing — apply sql/mt5_bridge_columns.sql and sql/lock_columns.sql")

    log.info(
        "Opened %s %s ticket=%s volume=%s fill=%s sl=%s tp=%s",
        direction,
        symbol,
        ticket,
        result.volume,
        fill_price,
        result.sl,
        result.tp,
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
                "ORPHAN: position %s open in MT5 but not managed (no status=open row)",
                ticket,
            )


def sync_trade(db: Client, mt5: MT5Client, trade: dict[str, Any]) -> None:
    ticket = int(trade["mt5_ticket"])
    direction = (trade.get("direction") or "").lower()
    # Journal pips use webhook entry (1:1 with Pine); fill is kept in mt5_fill_price.
    journal_entry = to_float(trade.get("entry"))
    fill_entry = to_float(trade.get("mt5_fill_price")) or journal_entry
    pos = mt5.get_position(ticket)

    if pos is not None:
        floating = round(float(pos.profit + pos.swap), 2)
        new_sl = float(pos.sl) if pos.sl else 0.0
        new_tp = float(pos.tp) if pos.tp else 0.0
        prev_profit = to_float(trade.get("mt5_profit"))
        if prev_profit is None:
            prev_profit = to_float(trade.get("usd_0_3"))
        prev_sl = to_float(trade.get("stop"))
        prev_tp = to_float(trade.get("tp"))

        profit_changed = prev_profit is None or abs(prev_profit - floating) >= 0.01
        sl_changed = prev_sl is None or abs(prev_sl - new_sl) >= 1e-6
        tp_changed = prev_tp is None or abs(prev_tp - new_tp) >= 1e-6

        if not (profit_changed or sl_changed or tp_changed):
            log.debug(
                "Open ticket=%s unchanged floating=%s lock=%s",
                ticket,
                floating,
                trade.get("lock_state") or "none",
            )
            return

        payload = {
            "stop": pos.sl,
            "tp": pos.tp,
            "mt5_profit": floating,
        }
        try:
            update_trade(db, trade["id"], payload)
        except Exception:
            payload.pop("mt5_profit", None)
            payload["usd_0_3"] = floating
            update_trade(db, trade["id"], payload)
        log.info("Open ticket=%s floating=%s lock=%s", ticket, floating, trade.get("lock_state") or "none")
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
    exit_dt = datetime.fromtimestamp(last.time, tz=timezone.utc)
    mt5_reason = deal_exit_reason(int(last.reason))
    exit_reason = resolve_exit_reason(trade, exit_price=exit_price, mt5_reason=mt5_reason)
    pips = calc_pips(direction, journal_entry or fill_entry, exit_price)
    payload = {
        "status": "closed",
        "exit": exit_price,
        "exit_time": iso(exit_dt),
        "exit_reason": exit_reason,
        "pips": pips,
        "usd_0_3": profit,
        "mt5_close_price": exit_price,
        "mt5_closed_at": iso(exit_dt),
        "mt5_profit": profit,
    }
    try:
        update_trade(db, trade["id"], payload)
    except Exception:
        for key in ("mt5_close_price", "mt5_closed_at", "mt5_profit"):
            payload.pop(key, None)
        update_trade(db, trade["id"], payload)
        log.warning("mt5 close columns missing — wrote classic close fields only")

    log.info(
        "Closed ticket=%s reason=%s exit=%s profit=%s pips=%s",
        ticket,
        exit_reason,
        exit_price,
        profit,
        pips,
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
    log.info(
        "Bot started. poll=%ss dry_run=%s lot=%s symbol=%s",
        poll_sec,
        dry_run,
        env_float("LOT_SIZE", 0.01),
        os.getenv("MT5_SYMBOL", "XAUUSD"),
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
