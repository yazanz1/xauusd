"""Day after NYSE full-day holiday: new entries only until 10:00 IDT.

Scope: new entries only (before MT5 order_send). Open trades continue.
All signal colors. Short sessions (day after Thanksgiving, Christmas Eve) are not holidays.

Day detection uses America/New_York:
  D = today's NY date; P = previous weekday; if P is a full NYSE holiday → filter day.
Entry cutoff uses Asia/Jerusalem: allowed before 10:00:00, blocked from 10:00:00.

NYSE full-day holidays (observed Sat→Fri, Sun→Mon for fixed dates):
New Year, MLK, Presidents' Day, Good Friday, Memorial Day, Juneteenth,
Independence Day, Labor Day, Thanksgiving, Christmas.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from bot1_filters import FilterResult

TZ_IDT = ZoneInfo("Asia/Jerusalem")
TZ_NY = ZoneInfo("America/New_York")
NAME = "us_holiday_next_day"
ENTRY_UNTIL = time(10, 0)


def _observe(d: date) -> date:
    """NYSE observed: Sat → Fri, Sun → Mon."""
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
        return d + timedelta(days=shift + 7 * (n - 1))
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


def _prev_weekday(d: date) -> date:
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


@lru_cache(maxsize=8)
def nyse_full_holidays(year: int) -> dict[date, str]:
    """Observed NYSE full-day holiday date → name for `year` (+ NYE spill)."""
    out: dict[date, str] = {}

    def add_fixed(month: int, day: int, name: str, y: int = year) -> None:
        out[_observe(date(y, month, day))] = name

    add_fixed(1, 1, "New Year's Day")
    add_fixed(1, 1, "New Year's Day", year + 1)

    out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    out[_nth_weekday(year, 2, 0, 3)] = "Presidents' Day"
    out[_easter_sunday(year) - timedelta(days=2)] = "Good Friday"
    out[_nth_weekday(year, 5, 0, -1)] = "Memorial Day"
    add_fixed(6, 19, "Juneteenth")
    add_fixed(7, 4, "Independence Day")
    out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving"
    add_fixed(12, 25, "Christmas")

    return {d: name for d, name in out.items() if d.year == year}


def _holiday_on(d: date) -> str | None:
    return nyse_full_holidays(d.year).get(d)


def day_after_holiday(now: datetime | None = None) -> tuple[date, str, date] | None:
    """If NY date D is the first weekday session after a full holiday, return (D, name, P)."""
    ny_now = (now or datetime.now(TZ_NY)).astimezone(TZ_NY)
    d = ny_now.date()
    if d.weekday() >= 5:
        return None  # weekends are not the first trading session after a holiday
    p = _prev_weekday(d)
    name = _holiday_on(p)
    if name is None:
        return None
    return d, name, p


def _idt_now(now: datetime | None) -> datetime:
    return (now or datetime.now(TZ_IDT)).astimezone(TZ_IDT)


class UsHolidayNextDayFilter:
    name = NAME

    def check(self, signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> FilterResult:
        now = context.get("now") if context else None
        info = day_after_holiday(now)
        if info is None:
            return FilterResult(allowed=True)

        _d, holiday_name, holiday_date = info
        idt = _idt_now(now)
        if idt.time() < ENTRY_UNTIL:
            return FilterResult(allowed=True)

        return FilterResult(
            allowed=False,
            reason=(
                f"after 10:00 Asia/Jerusalem on day after US holiday "
                f"{holiday_name} ({holiday_date.isoformat()})"
            ),
        )
