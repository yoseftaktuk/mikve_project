"""Hebrew month helper for subscription purchase comments / health."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from pyluach import dates

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


@dataclass(frozen=True)
class HebrewMonth:
    year: int
    month: int
    name: str


def current_hebrew_month() -> HebrewMonth:
    day = datetime.now(ISRAEL_TZ).date()
    hd = dates.HebrewDate.from_pydate(day)
    return HebrewMonth(year=int(hd.year), month=int(hd.month), name=str(hd.month_name(hebrew=True)))
