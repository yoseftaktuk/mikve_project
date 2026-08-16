"""Hebrew calendar helpers for monthly subscriptions (Israel local time)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from pyluach import dates

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
FRIDAY = 4
NOON = time(12, 0, 0)
SPECIAL_DAY_LIMIT = 2
NORMAL_DAY_LIMIT = 1

# pyluach English names for work-forbidden festivals that have a real eve.
EREV_FESTIVAL_NAMES = frozenset(
    {
        "Rosh Hashana",
        "Yom Kippur",
        "Succos",
        "Shmini Atzeres",
        "Simchas Torah",
        "Pesach",
        "Shavuos",
    }
)


@dataclass(frozen=True)
class HebrewMonth:
    """Current (or given) Hebrew month identity for subscription periods."""

    year: int
    month: int
    name: str


def israel_now(now: datetime | None = None) -> datetime:
    """Current (or given) instant as an aware Asia/Jerusalem datetime."""
    if now is None:
        return datetime.now(ISRAEL_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=ISRAEL_TZ)
    return now.astimezone(ISRAEL_TZ)


def israel_today(now: datetime | None = None) -> date:
    """Civil calendar date in Asia/Jerusalem."""
    return israel_now(now).date()


def israel_date_from_timestamp(ts: datetime) -> date:
    """Civil Israel date for a stored timestamp (UTC or aware)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ISRAEL_TZ).date()


def hebrew_date_for(day: date | None = None, *, now: datetime | None = None) -> dates.HebrewDate:
    """pyluach HebrewDate for an Israel civil date (default: today)."""
    target = day or israel_today(now)
    return dates.HebrewDate.from_pydate(target)


def hebrew_month_for(day: date | None = None, *, now: datetime | None = None) -> HebrewMonth:
    """Hebrew year/month/name for an Israel civil date (default: today)."""
    hd = hebrew_date_for(day, now=now)
    return HebrewMonth(year=int(hd.year), month=int(hd.month), name=str(hd.month_name(hebrew=True)))


def is_friday(now: datetime | None = None) -> bool:
    """True when the Israel local weekday is Friday."""
    return israel_now(now).weekday() == FRIDAY


def is_erev_holiday(day: date | None = None, *, now: datetime | None = None) -> bool:
    """True when the next Hebrew day is a listed Israel Yom Tov (not Chol HaMoed)."""
    hd = hebrew_date_for(day, now=now)
    tomorrow = hd + 1
    # festival(..., include_working_days=False) is Yom Tov only. holiday() also
    # returns Chol HaMoed as "Pesach"/"Succos" and must not count as an eve.
    festival = tomorrow.festival(israel=True, include_working_days=False)
    if festival in EREV_FESTIVAL_NAMES:
        return True
    fast = tomorrow.fast_day()
    return fast in EREV_FESTIVAL_NAMES


def is_special_entry_day(now: datetime | None = None) -> bool:
    """Friday or the eve of a listed Jewish holiday (Israel civil day)."""
    local = israel_now(now)
    return is_friday(local) or is_erev_holiday(local.date())


def entry_day_kind(now: datetime | None = None) -> str:
    """Label for logs: normal / friday / erev_holiday / friday_erev_holiday."""
    local = israel_now(now)
    friday = is_friday(local)
    erev = is_erev_holiday(local.date())
    if friday and erev:
        return "friday_erev_holiday"
    if friday:
        return "friday"
    if erev:
        return "erev_holiday"
    return "normal"


def daily_free_entry_limit(now: datetime | None = None) -> int:
    """Free membership entries allowed for this Israel local instant."""
    local = israel_now(now)
    if is_special_entry_day(local) and local.time() >= NOON:
        return SPECIAL_DAY_LIMIT
    return NORMAL_DAY_LIMIT
