"""Daily / London / NY Gann levels — same formulas as gan.pine, londongan.pine, NYgan.pine."""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from supabase import Client

from mt5_client import MT5Client

TABLE = "gann_levels"
SQ9_STEP = 0.25
SQ9_COUNT = 4
DAILY_EIGHTH_RANGE = 200.0
SESSION_EIGHTH_RANGE = 150.0
DATE_TZ = ZoneInfo("Asia/Jerusalem")
LONDON_TZ = ZoneInfo("Europe/London")
NY_TZ = ZoneInfo("America/New_York")

log = logging.getLogger("xaubot.gann")

# Avoid repeat SELECT id once a session grid is known saved for the day.
_saved_cache: set[tuple[str, str]] = set()
_saved_cache_day: str | None = None


def _cache_reset_if_new_day(date_idt: str) -> None:
    global _saved_cache_day, _saved_cache
    if _saved_cache_day != date_idt:
        _saved_cache = set()
        _saved_cache_day = date_idt


def _already_saved(db: Client, date_idt: str, session: str) -> bool:
    _cache_reset_if_new_day(date_idt)
    key = (date_idt, session)
    if key in _saved_cache:
        return True
    result = (
        db.table(TABLE)
        .select("id")
        .eq("date_idt", date_idt)
        .eq("session", session)
        .limit(1)
        .execute()
    )
    if result.data:
        _saved_cache.add(key)
        return True
    return False


def _mark_saved(date_idt: str, session: str) -> None:
    _cache_reset_if_new_day(date_idt)
    _saved_cache.add((date_idt, session))


def compute_levels(base: float, eighth_range: float, *, step: float = SQ9_STEP, count: int = SQ9_COUNT) -> dict[str, float]:
    root = math.sqrt(base)
    eighth = eighth_range / 8.0
    levels: dict[str, float] = {}
    for n in range(1, count + 1):
        levels[f"sq9_u{n}"] = round((root + step * n) ** 2, 2)
        levels[f"sq9_d{n}"] = round((root - step * n) ** 2, 2)
        levels[f"eighth_u{n}"] = round(base + eighth * n, 2)
        levels[f"eighth_d{n}"] = round(base - eighth * n, 2)
    return levels


def _israel_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(DATE_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=DATE_TZ)
    return now.astimezone(DATE_TZ)


def _session_open(tz: ZoneInfo, hour: int, minute: int, on_date) -> datetime:
    """Clock time in `tz` on the Israel trading date (not the session TZ's calendar date)."""
    return datetime.combine(on_date, time(hour, minute), tzinfo=tz)


def _save(
    db: Client,
    *,
    date_idt: str,
    session: str,
    symbol: str,
    base_price: float,
    base_time: datetime,
    eighth_range: float,
) -> None:
    levels = compute_levels(base_price, eighth_range)
    payload: dict[str, Any] = {
        "date_idt": date_idt,
        "session": session,
        "symbol": symbol,
        "base_price": round(base_price, 2),
        "base_time": base_time.astimezone(DATE_TZ).isoformat(),
        "eighth_range": eighth_range,
        "error": None,
        "updated_at": datetime.now(DATE_TZ).isoformat(),
        **levels,
    }
    db.table(TABLE).upsert(payload, on_conflict="date_idt,session").execute()
    _mark_saved(date_idt, session)
    log.info(
        "Saved %s %s base=%s eighth_range=%s u1=%s d1=%s",
        date_idt,
        session,
        payload["base_price"],
        eighth_range,
        levels["sq9_u1"],
        levels["sq9_d1"],
    )


def _snapshot_daily(db: Client, mt5: MT5Client, symbol: str, date_idt: str) -> None:
    if _already_saved(db, date_idt, "daily"):
        return
    got = mt5.daily_prev_close(symbol)
    if got is None:
        log.warning("No completed daily bar yet for %s", date_idt)
        return
    base, bar_time = got
    _save(
        db,
        date_idt=date_idt,
        session="daily",
        symbol=symbol,
        base_price=base,
        base_time=bar_time,
        eighth_range=DAILY_EIGHTH_RANGE,
    )


def _snapshot_session(
    db: Client,
    mt5: MT5Client,
    symbol: str,
    date_idt: str,
    session: str,
    open_at: datetime,
    eighth_range: float,
    now: datetime,
) -> None:
    if now < open_at:
        return
    if _already_saved(db, date_idt, session):
        return
    got = mt5.m1_open_at(symbol, open_at)
    if got is None:
        log.warning("No M1 bar at %s for %s %s — retry next scan", open_at.isoformat(), date_idt, session)
        return
    base, bar_time = got
    _save(
        db,
        date_idt=date_idt,
        session=session,
        symbol=symbol,
        base_price=base,
        base_time=bar_time,
        eighth_range=eighth_range,
    )


def sync_gann_levels(db: Client, mt5: MT5Client, now: datetime | None = None) -> None:
    """Write today's daily / London / NY grids once each, when the base price exists.

    Does not open trades. Safe to call every 5-minute scan; skips rows already saved.
    """
    symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
    now = _israel_now(now)
    date_idt = now.date().isoformat()
    weekday = now.weekday()  # Mon=0 ... Sun=6

    try:
        _snapshot_daily(db, mt5, symbol, date_idt)
    except Exception:
        log.exception("Daily Gann snapshot failed")

    if weekday >= 5:
        return

    trading_date = now.date()
    london_open = _session_open(LONDON_TZ, 8, 0, trading_date)
    ny_open = _session_open(NY_TZ, 8, 30, trading_date)

    try:
        _snapshot_session(db, mt5, symbol, date_idt, "london", london_open, SESSION_EIGHTH_RANGE, now)
    except Exception:
        log.exception("London Gann snapshot failed")

    try:
        _snapshot_session(db, mt5, symbol, date_idt, "ny", ny_open, SESSION_EIGHTH_RANGE, now)
    except Exception:
        log.exception("NY Gann snapshot failed")
