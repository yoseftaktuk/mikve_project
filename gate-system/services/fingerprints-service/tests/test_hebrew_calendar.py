from datetime import date

from app.hebrew_calendar import hebrew_month_for


def test_hebrew_month_for_known_date() -> None:
    # 2026-08-06 was during Av 5786 (verified via pyluach).
    month = hebrew_month_for(date(2026, 8, 6))
    assert month.year == 5786
    assert month.month == 5
    assert month.name == "אב"
