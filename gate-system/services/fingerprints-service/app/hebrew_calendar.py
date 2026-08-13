"""Hebrew calendar helpers for monthly subscriptions (Israel local time)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pyluach import dates

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


@dataclass(frozen=True)
class HebrewMonth:
    """Current (or given) Hebrew month identity for subscription periods."""

    year: int
    month: int
    name: str


def israel_today() -> date:
    """Civil calendar date in Asia/Jerusalem."""
    return datetime.now(ISRAEL_TZ).date()


def hebrew_month_for(day: date | None = None) -> HebrewMonth:
    """Hebrew year/month/name for an Israel civil date (default: today)."""
    target = day or israel_today()
    hd = dates.HebrewDate.from_pydate(target)
    return HebrewMonth(year=int(hd.year), month=int(hd.month), name=str(hd.month_name(hebrew=True)))
