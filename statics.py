"""Daily gold trade statistics → public.gold_statics.

At end of each Israel trading day (after midnight), scans gold_trades for that
date_idt and upserts counts: tp / sl / lock + profit + pips.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from supabase import Client

TRADES_TABLE = "gold_trades"
STATICS_TABLE = "gold_statics"
DATE_TZ = ZoneInfo("Asia/Jerusalem")
# Finalize previous day once we are past this hour:minute (Israel).
FINALIZE_AFTER = (0, 5)

log = logging.getLogger("xaubot.statics")

# Skip repeat SELECT/upsert once yesterday is finalized this process.
_saved_days: set[str] = set()


def _israel_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(DATE_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=DATE_TZ)
    return now.astimezone(DATE_TZ)


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def compute_day_stats(db: Client, date_idt: str | date) -> dict[str, Any]:
    """Aggregate closed/open trades for one Israel trading date."""
    day = date_idt.isoformat() if isinstance(date_idt, date) else str(date_idt)
    result = (
        db.table(TRADES_TABLE)
        .select("status, exit_reason, pips, usd_0_3, mt5_profit")
        .eq("date_idt", day)
        .execute()
    )
    rows = result.data or []

    trades_total = len(rows)
    trades_closed = 0
    trades_open = 0
    count_tp = 0
    count_sl = 0
    count_lock = 0
    count_other = 0
    profit_usd = 0.0
    pips_total = 0.0

    for row in rows:
        status = (row.get("status") or "").lower()
        if status == "open":
            trades_open += 1
            continue

        trades_closed += 1
        reason = (row.get("exit_reason") or "").lower()
        if reason == "tp":
            count_tp += 1
        elif reason == "sl":
            count_sl += 1
        elif reason == "lock":
            count_lock += 1
        else:
            count_other += 1

        # Prefer journal usd_0_3; fall back to mt5_profit.
        profit = row.get("usd_0_3")
        if profit is None:
            profit = row.get("mt5_profit")
        profit_usd += _to_float(profit)
        pips_total += _to_float(row.get("pips"))

    return {
        "date_idt": day,
        "trades_total": trades_total,
        "trades_closed": trades_closed,
        "trades_open": trades_open,
        "count_tp": count_tp,
        "count_sl": count_sl,
        "count_lock": count_lock,
        "count_other": count_other,
        "profit_usd": round(profit_usd, 2),
        "pips_total": round(pips_total, 2),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _already_saved(db: Client, date_idt: str) -> bool:
    if date_idt in _saved_days:
        return True
    result = (
        db.table(STATICS_TABLE)
        .select("id")
        .eq("date_idt", date_idt)
        .limit(1)
        .execute()
    )
    if result.data:
        _saved_days.add(date_idt)
        return True
    return False


def upsert_day_stats(db: Client, date_idt: str | date, *, force: bool = False) -> dict[str, Any] | None:
    """Write one day into gold_statics. Skip if already present unless force=True."""
    day = date_idt.isoformat() if isinstance(date_idt, date) else str(date_idt)
    if not force and _already_saved(db, day):
        log.debug("Statics already saved for %s", day)
        return None

    stats = compute_day_stats(db, day)
    db.table(STATICS_TABLE).upsert(stats, on_conflict="date_idt").execute()
    _saved_days.add(day)
    log.info(
        "Statics %s: total=%s closed=%s tp=%s sl=%s lock=%s other=%s profit=%s pips=%s",
        day,
        stats["trades_total"],
        stats["trades_closed"],
        stats["count_tp"],
        stats["count_sl"],
        stats["count_lock"],
        stats["count_other"],
        stats["profit_usd"],
        stats["pips_total"],
    )
    return stats


def sync_daily_statics(db: Client, now: datetime | None = None) -> None:
    """After Israel midnight (+FINALIZE_AFTER), finalize yesterday once.

    Safe to call every poll — no-op until end-of-day window, then idempotent.
    """
    now = _israel_now(now)
    after_h, after_m = FINALIZE_AFTER
    minutes = now.hour * 60 + now.minute
    if minutes < after_h * 60 + after_m:
        return

    yesterday = (now.date() - timedelta(days=1)).isoformat()
    # Skip weekends with zero trades? Still write a row if any trades exist;
    # always write so the day is marked done (even zeros).
    try:
        upsert_day_stats(db, yesterday, force=False)
    except Exception:
        log.exception("Failed to write gold_statics for %s", yesterday)
