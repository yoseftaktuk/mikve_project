from __future__ import annotations

from datetime import date, datetime, timezone

from pyluach import dates

from app.hebrew_calendar import (
    ISRAEL_TZ,
    daily_free_entry_limit,
    entry_day_kind,
    hebrew_month_for,
    israel_date_from_timestamp,
    is_erev_holiday,
    is_friday,
    is_special_entry_day,
)


def israel_at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ISRAEL_TZ)


def test_hebrew_month_for_av_5786():
    month = hebrew_month_for(date(2026, 8, 6))
    assert month.year == 5786
    assert month.month == 5
    assert month.name == "אב"


def test_friday_before_noon_limit_is_one():
    now = israel_at(date(2026, 8, 14), 11, 59, 59)
    assert is_friday(now)
    assert not is_erev_holiday(now.date())
    assert daily_free_entry_limit(now) == 1


def test_friday_noon_limit_is_two():
    now = israel_at(date(2026, 8, 14), 12, 0, 0)
    assert daily_free_entry_limit(now) == 2
    assert daily_free_entry_limit(israel_at(date(2026, 8, 14), 12, 0, 1)) == 2


def test_saturday_has_no_friday_allowance():
    now = israel_at(date(2026, 8, 15), 15, 0, 0)
    assert not is_friday(now)
    assert not is_erev_holiday(now.date())
    assert daily_free_entry_limit(now) == 1


def test_erev_yom_kippur_after_noon_limit_is_two():
    now = israel_at(date(2026, 9, 20), 14, 0, 0)
    assert is_erev_holiday(now.date())
    assert daily_free_entry_limit(now) == 2
    assert daily_free_entry_limit(israel_at(date(2026, 9, 20), 11, 0, 0)) == 1


def test_yom_kippur_day_has_no_extra_entry():
    now = israel_at(date(2026, 9, 21), 15, 0, 0)
    assert not is_erev_holiday(now.date())
    assert daily_free_entry_limit(now) == 1


def test_chol_hamoed_without_erev_has_no_extra_entry():
    now = israel_at(date(2026, 4, 6), 15, 0, 0)
    hd = dates.HebrewDate.from_pydate(now.date())
    assert hd.holiday(israel=True) == "Pesach"
    assert hd.festival(israel=True, include_working_days=False) is None
    assert not is_erev_holiday(now.date())
    assert daily_free_entry_limit(now) == 1


def test_fast_day_has_no_special_treatment():
    now = israel_at(date(2026, 7, 23), 15, 0, 0)
    assert dates.HebrewDate.from_pydate(now.date()).fast_day() == "9 of Av"
    assert not is_special_entry_day(now)
    assert daily_free_entry_limit(now) == 1


def test_friday_that_is_also_erev_holiday_still_caps_at_two():
    now = israel_at(date(2026, 9, 11), 16, 0, 0)
    assert is_friday(now)
    assert is_erev_holiday(now.date())
    assert entry_day_kind(now) == "friday_erev_holiday"
    assert daily_free_entry_limit(now) == 2


def test_israel_date_from_utc_around_midnight():
    # IDT is UTC+3 in August: 21:00 UTC on the 14th is 00:00 on the 15th in Israel.
    friday_late = datetime(2026, 8, 14, 20, 59, 59, tzinfo=timezone.utc)
    saturday_start = datetime(2026, 8, 14, 21, 0, 0, tzinfo=timezone.utc)
    assert israel_date_from_timestamp(friday_late) == date(2026, 8, 14)
    assert israel_date_from_timestamp(saturday_start) == date(2026, 8, 15)


def test_dst_spring_forward_friday_noon_uses_jerusalem():
    now = israel_at(date(2026, 3, 27), 12, 0, 0)
    assert is_friday(now)
    assert daily_free_entry_limit(now) == 2
    assert daily_free_entry_limit(israel_at(date(2026, 3, 27), 11, 59, 59)) == 1
