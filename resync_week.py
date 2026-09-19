"""One-shot: recompute closes from MT5 position deals (from 2026-09-14).

Default is DRY-RUN (no writes). To apply:

  python resync_week.py                     # compare only (default)
  python resync_week.py --apply             # write after you reviewed dry-run

Uses history_deals_by_position + resolve_exit_reason (same as bot sync fix).
Does NOT touch open positions that still have no OUT deal.
Does NOT touch watchdog.ps1.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from typing import Any

from dotenv import load_dotenv

from bot import (
    DEAL_ENTRY_INOUT,
    DEAL_ENTRY_OUT,
    TABLE,
    calc_pips,
    connect_supabase,
    deal_exit_reason,
    iso,
    to_float,
    update_trade,
)
from lock import resolve_exit_reason
from mt5_client import MT5Client, MT5Error
from sessions import SessionManager

log = logging.getLogger("xaubot.resync")

FROM_DATE = date(2026, 9, 14)


def fetch_copied_since(db, from_date: date) -> list[dict[str, Any]]:
    result = (
        db.table(TABLE)
        .select("*")
        .gte("date_idt", from_date.isoformat())
        .not_.is_("mt5_ticket", "null")
        .order("date_idt")
        .order("copied_at")
        .execute()
    )
    return list(result.data or [])


def recompute_from_deals(mt5: MT5Client, trade: dict[str, Any]) -> dict[str, Any] | None:
    """Return proposed close fields, or None if position still open / no OUT deal."""
    ticket = int(trade["mt5_ticket"])
    direction = (trade.get("direction") or "").lower()
    journal_entry = to_float(trade.get("entry"))
    fill_entry = to_float(trade.get("mt5_fill_price")) or journal_entry

    if mt5.get_position(ticket) is not None:
        return None  # still open — do not invent a close

    deals = mt5.history_deals_by_position(ticket)
    if any(int(d.position_id) != ticket for d in deals):
        raise RuntimeError(f"ticket {ticket}: position_id mismatch in deals")

    out_deals = [d for d in deals if d.entry in (DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT)]
    if not out_deals:
        return None

    last = max(out_deals, key=lambda d: d.time)
    if int(last.position_id) != ticket:
        raise RuntimeError(f"ticket {ticket}: exit deal position_id={last.position_id}")

    profit = round(
        sum(d.profit + d.swap + getattr(d, "commission", 0.0) for d in deals),
        2,
    )
    exit_price = float(last.price)
    exit_dt = datetime.fromtimestamp(last.time, tz=timezone.utc)
    mt5_reason = deal_exit_reason(int(last.reason))
    exit_reason = resolve_exit_reason(trade, exit_price=exit_price, mt5_reason=mt5_reason)
    pips = calc_pips(direction, journal_entry or fill_entry, exit_price)

    return {
        "status": "closed",
        "exit": exit_price,
        "exit_time": iso(exit_dt),
        "exit_reason": exit_reason,
        "pips": pips,
        "usd_0_3": profit,
        "mt5_close_price": exit_price,
        "mt5_closed_at": iso(exit_dt),
        "mt5_profit": profit,
        "_mt5_reason": mt5_reason,
        "_deal_time": exit_dt,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _profit_changed(a: Any, b: Any, tol: float = 0.01) -> bool:
    fa, fb = to_float(a), to_float(b)
    if fa is None and fb is None:
        return False
    if fa is None or fb is None:
        return True
    return abs(fa - fb) >= tol


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Resync gold_trades closes from MT5 deals")
    parser.add_argument(
        "--from-date",
        default=FROM_DATE.isoformat(),
        help=f"date_idt >= this (default {FROM_DATE})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write updates to Supabase (default is dry-run — no writes)",
    )
    args = parser.parse_args(argv)
    apply = bool(args.apply)
    dry_run = not apply

    from_date = date.fromisoformat(args.from_date)
    stamp = date.today().isoformat()
    resync_tag = f"resynced {stamp}"

    db = connect_supabase()
    rows = fetch_copied_since(db, from_date)
    log.info("Loaded %s rows with mt5_ticket since %s", len(rows), from_date)

    session = SessionManager()
    try:
        mt5 = session.open()
    except MT5Error as exc:
        log.error("%s", exc)
        return 1

    print()
    print(
        f"{'ticket':<12} {'old_reason':<10} {'new_reason':<10} "
        f"{'old_pnl':>10} {'new_pnl':>10} {'old_exit':>10} {'new_exit':>10} {'note'}"
    )
    print("-" * 100)

    reason_changes = 0
    profit_changes = 0
    skip_open = 0
    skip_no_deal = 0
    errors = 0
    would_write = 0
    wrote = 0
    new_profit_sum = 0.0
    old_profit_sum = 0.0
    comparable = 0

    for trade in rows:
        ticket = int(trade["mt5_ticket"])
        old_reason = trade.get("exit_reason")
        old_profit = to_float(trade.get("mt5_profit"))
        if old_profit is None:
            old_profit = to_float(trade.get("usd_0_3"))
        old_exit = to_float(trade.get("exit")) or to_float(trade.get("mt5_close_price"))

        try:
            proposed = recompute_from_deals(mt5, trade)
        except Exception as exc:
            errors += 1
            print(f"{ticket:<12} {'ERR':<10} {'':<10} {'':>10} {'':>10} {'':>10} {'':>10} {exc}")
            log.exception("ticket %s", ticket)
            continue

        if proposed is None:
            if mt5.get_position(ticket) is not None:
                skip_open += 1
                note = "STILL_OPEN"
            else:
                skip_no_deal += 1
                note = "NO_OUT_DEAL"
            print(
                f"{ticket:<12} {_fmt(old_reason):<10} {'—':<10} "
                f"{_fmt(old_profit):>10} {'—':>10} {_fmt(old_exit):>10} {'—':>10} {note}"
            )
            continue

        new_reason = proposed["exit_reason"]
        new_profit = float(proposed["mt5_profit"])
        new_exit = float(proposed["exit"])

        comparable += 1
        if old_profit is not None:
            old_profit_sum += old_profit
        new_profit_sum += new_profit

        reason_diff = (old_reason or "") != (new_reason or "")
        profit_diff = _profit_changed(old_profit, new_profit)
        exit_diff = _profit_changed(old_exit, new_exit, tol=0.005)
        any_diff = reason_diff or profit_diff or exit_diff or (trade.get("status") != "closed")

        if reason_diff:
            reason_changes += 1
        if profit_diff:
            profit_changes += 1

        notes: list[str] = []
        if reason_diff:
            notes.append("REASON")
        if profit_diff:
            notes.append("PNL")
        if exit_diff:
            notes.append("EXIT")
        if trade.get("status") != "closed":
            notes.append("WAS_" + str(trade.get("status") or "?").upper())
        if not notes:
            notes.append("same")

        print(
            f"{ticket:<12} {_fmt(old_reason):<10} {_fmt(new_reason):<10} "
            f"{_fmt(old_profit):>10} {_fmt(new_profit):>10} "
            f"{_fmt(old_exit):>10} {_fmt(new_exit):>10} {','.join(notes)}"
        )

        if not any_diff:
            continue

        would_write += 1
        if dry_run:
            continue

        payload = {
            "status": proposed["status"],
            "exit": proposed["exit"],
            "exit_time": proposed["exit_time"],
            "exit_reason": proposed["exit_reason"],
            "pips": proposed["pips"],
            "usd_0_3": proposed["usd_0_3"],
            "mt5_close_price": proposed["mt5_close_price"],
            "mt5_closed_at": proposed["mt5_closed_at"],
            "mt5_profit": proposed["mt5_profit"],
            "mt5_error": resync_tag,
        }
        try:
            update_trade(db, trade["id"], payload)
            wrote += 1
        except Exception:
            errors += 1
            log.exception("Failed to write ticket %s", ticket)

    print("-" * 100)
    print()
    mode = "DRY-RUN (no writes)" if dry_run else f"APPLY (mt5_error='{resync_tag}')"
    print(f"Mode:              {mode}")
    print(f"Rows scanned:      {len(rows)}")
    print(f"Recomputed:        {comparable}")
    print(f"Still open:        {skip_open}")
    print(f"No OUT deal:       {skip_no_deal}")
    print(f"Errors:            {errors}")
    print(f"exit_reason diffs: {reason_changes}")
    print(f"mt5_profit diffs:  {profit_changes}")
    print(f"Would write:       {would_write}")
    if apply:
        print(f"Wrote:             {wrote}")
    print(f"Old pnl sum*:      {old_profit_sum:.2f}")
    print(f"New pnl sum*:      {new_profit_sum:.2f}")
    print(f"Delta*:            {new_profit_sum - old_profit_sum:.2f}")
    print("  (* sums over rows that had a recomputed OUT deal)")
    print()
    if dry_run:
        print("Review the table above. If OK, run:")
        print(f"  python resync_week.py --apply --from-date {from_date.isoformat()}")
    else:
        print("Done. Spot-check a few tickets in Supabase / MT5.")

    session.close()
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
