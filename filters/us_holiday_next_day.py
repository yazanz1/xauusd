"""Day after US market holiday: new entries only until 10:00 IDT.

Applies on the next weekday after the observed US holiday (banks/equities).
Open positions are not closed by this filter — new entries only.

Holidays (observed Sat→Fri, Sun→Mon for fixed dates):
New Year, MLK, Presidents' Day, Good Friday, Memorial Day, Juneteenth,
Independence Day, Labor Day, Columbus/Indigenous Peoples' Day,
Veterans Day, Thanksgiving, Christmas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from filters import FilterResult

TZ = ZoneInfo("Asia/Jerusalem")
NAME = "us_holiday_next_day"
ENTRY_UNTIL = time(10, 0)


def _observe(d: date) -> date:
    """US federal observed: Sat → Fri, Sun → Mon."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Mon=0 … Sun=6. n is 1-based; n=-1 → last in month."""
    if n > 0:
        d = date(year, month, 1)
        shift = (weekday - d.weekday()) % 7
        d += timedelta(days=shift + 7 * (n - 1))
        return d
    # last weekday
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    shift = (d.weekday() - weekday) % 7
    return d - timedelta(days=shift)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _next_weekday(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


@lru_cache(maxsize=8)
def observed_us_holidays(year: int) -> frozenset[date]:
    """Observed US equity/bank holiday dates for `year` (and NYE spill from prior year)."""
    out: set[date] = set()

    def add_fixed(month: int, day: int, y: int = year) -> None:
        out.add(_observe(date(y, month, day)))

    add_fixed(1, 1)
    # New Year can observe into prior December (Sat Jan 1 → Fri Dec 31)
    add_fixed(1, 1, year + 1)

    out.add(_nth_weekday(year, 1, 0, 3))  # MLK — 3rd Monday Jan
    out.add(_nth_weekday(year, 2, 0, 3))  # Presidents' — 3rd Monday Feb
    out.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
    out.add(_nth_weekday(year, 5, 0, -1))  # Memorial — last Monday May
    add_fixed(6, 19)  # Juneteenth
    add_fixed(7, 4)  # Independence
    out.add(_nth_weekday(year, 9, 0, 1))  # Labor — 1st Monday Sep
    out.add(_nth_weekday(year, 10, 0, 2))  # Columbus — 2nd Monday Oct
    add_fixed(11, 11)  # Veterans
    out.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving — 4th Thursday Nov
    add_fixed(12, 25)  # Christmas

    return frozenset(d for d in out if d.year == year)


@lru_cache(maxsize=8)
def post_holiday_entry_dates(year: int) -> frozenset[date]:
    """Weekdays when the post-holiday early-entry rule applies."""
    days: set[date] = set()
    for h in observed_us_holidays(year):
        days.add(_next_weekday(h))
    # Holiday observed Dec 31 prior year → next weekday may fall in `year`
    for h in observed_us_holidays(year - 1):
        nxt = _next_weekday(h)
        if nxt.year == year:
            days.add(nxt)
    return frozenset(days)


def _local_now(now: datetime | None) -> datetime:
    return (now or datetime.now(TZ)).astimezone(TZ)


def is_post_holiday_cutoff(now: datetime | None = None) -> bool:
    """True when today is a post-holiday day and local time is >= 10:00 IDT."""
    local = _local_now(now)
    if local.date() not in post_holiday_entry_dates(local.year):
        return False
    return local.time() >= ENTRY_UNTIL


class UsHolidayNextDayFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        now = context.get("now") if context else None
        if not is_post_holiday_cutoff(now):
            return FilterResult(allowed=True)
        local = _local_now(now)
        return FilterResult(
            allowed=False,
            reason=(
                f"day after US holiday: no new entries after "
                f"{ENTRY_UNTIL.strftime('%H:%M')} IDT (now {local.strftime('%Y-%m-%d %H:%M')})"
            ),
        )
