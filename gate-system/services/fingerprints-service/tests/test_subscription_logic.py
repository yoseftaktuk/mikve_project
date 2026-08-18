from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.sql.selectable import Select

from app.hebrew_calendar import ISRAEL_TZ, hebrew_month_for
from app.models import STATUS_SUB_ACTIVE, Member, MemberActivity, MonthlySubscription
from app.subscription_logic import (
    EVENT_FREE_ENTRY,
    activate_subscription,
    mark_free_entry,
    subscription_snapshot,
)
from gate_shared.errors import AppError


def israel_at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=ISRAEL_TZ)


NORMAL = date(2026, 8, 13)  # Thursday in Av
FRIDAY = date(2026, 8, 14)
SATURDAY = date(2026, 8, 15)
EREV_YK = date(2026, 9, 20)
YK = date(2026, 9, 21)
EREV_PESACH = date(2026, 4, 1)
CHM_PESACH = date(2026, 4, 6)
AV_5786 = date(2026, 8, 6)
TISHREI_5787 = date(2026, 9, 15)
AV_5787 = date(2027, 8, 10)


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class FakeDb:
    def __init__(self) -> None:
        self.members: dict[uuid.UUID, Member] = {}
        self.subs: list[MonthlySubscription] = []
        self.activities: list[MemberActivity] = []
        self.commits = 0
        self.rollbacks = 0
        self._row_lock = asyncio.Lock()
        self._lock_held = False
        self._next_activity_id = 1

    def add(self, row: object) -> None:
        if isinstance(row, Member):
            self.members[row.id] = row
        elif isinstance(row, MonthlySubscription):
            if row.id is None:
                row.id = uuid.uuid4()
            self.subs.append(row)
        elif isinstance(row, MemberActivity):
            if row.id is None:
                row.id = self._next_activity_id
                self._next_activity_id += 1
            if row.created_at is None:
                row.created_at = datetime.now(timezone.utc)
            self.activities.append(row)
        else:
            raise TypeError(type(row))

    async def flush(self) -> None:
        return None

    def _release_lock(self) -> None:
        if self._lock_held:
            self._lock_held = False
            self._row_lock.release()

    async def commit(self) -> None:
        keys = [a.idempotency_key for a in self.activities if a.idempotency_key]
        if len(keys) != len(set(keys)):
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("duplicate", params=None, orig=Exception("duplicate"))
        self.commits += 1
        self._release_lock()

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._release_lock()

    async def refresh(self, row: object) -> None:
        return None

    async def get(self, model: type, key: object) -> object | None:
        if model is Member and isinstance(key, uuid.UUID):
            return self.members.get(key)
        return None

    def _match(self, stmt: Select) -> list[object]:
        entity = stmt.column_descriptions[0]["entity"]
        params = {str(k): v for k, v in stmt.compile().params.items()}
        if entity is MonthlySubscription:
            rows: list[object] = list(self.subs)
            for key, val in params.items():
                if "member_id" in key:
                    rows = [r for r in rows if isinstance(r, MonthlySubscription) and r.member_id == val]
                elif "hebrew_year" in key:
                    rows = [r for r in rows if isinstance(r, MonthlySubscription) and r.hebrew_year == val]
                elif "hebrew_month" in key:
                    rows = [r for r in rows if isinstance(r, MonthlySubscription) and r.hebrew_month == val]
                elif "status" in key:
                    rows = [r for r in rows if isinstance(r, MonthlySubscription) and r.status == val]
                elif "nedarim_transaction_id" in key:
                    rows = [r for r in rows if isinstance(r, MonthlySubscription) and r.nedarim_transaction_id == val]
            return rows
        if entity is MemberActivity:
            rows = list(self.activities)
            for key, val in params.items():
                if "idempotency_key" in key:
                    rows = [r for r in rows if isinstance(r, MemberActivity) and r.idempotency_key == val]
                elif "member_id" in key:
                    rows = [r for r in rows if isinstance(r, MemberActivity) and r.member_id == val]
                elif "event_type" in key:
                    rows = [r for r in rows if isinstance(r, MemberActivity) and r.event_type == val]
            return rows
        return []

    async def scalar(self, stmt: object) -> object | None:
        assert isinstance(stmt, Select)
        if getattr(stmt, "_for_update_arg", None) is not None:
            await self._row_lock.acquire()
            self._lock_held = True
        rows = self._match(stmt)
        return rows[0] if rows else None

    async def scalars(self, stmt: object) -> FakeScalarResult:
        assert isinstance(stmt, Select)
        return FakeScalarResult(self._match(stmt))


def _chip() -> Member:
    return Member(id=uuid.uuid4(), uid=f"FP-{uuid.uuid4().hex[:8]}", is_enabled=True)


def _sub(member_id: uuid.UUID, *, year: int, month: int, name: str, last_free: date | None = None) -> MonthlySubscription:
    return MonthlySubscription(
        id=uuid.uuid4(),
        member_id=member_id,
        hebrew_year=year,
        hebrew_month=month,
        hebrew_month_name=name,
        amount_cents=30000,
        nedarim_transaction_id=f"NED-{uuid.uuid4().hex[:8]}",
        status=STATUS_SUB_ACTIVE,
        last_free_entry_on=last_free,
        purchased_at=datetime.now(timezone.utc),
    )


def _seed_active(db: FakeDb, day: date, *, last_free: date | None = None) -> tuple[Member, MonthlySubscription]:
    chip = _chip()
    month = hebrew_month_for(day)
    sub = _sub(chip.id, year=month.year, month=month.month, name=month.name, last_free=last_free)
    db.add(chip)
    db.add(sub)
    return chip, sub


def _seed_used(db: FakeDb, member_id: uuid.UUID, when: datetime, token: str) -> None:
    db.add(
        MemberActivity(
            member_id=member_id,
            event_type=EVENT_FREE_ENTRY,
            delta_cents=0,
            description="seed",
            idempotency_key=f"sub-free:{token}",
            created_at=when.astimezone(timezone.utc),
        )
    )


async def _available(db: FakeDb, member_id: uuid.UUID, now: datetime) -> bool:
    snap = await subscription_snapshot(db, member_id, now=now)  # type: ignore[arg-type]
    return snap.subscription_free_entry_available_today


@pytest.mark.asyncio
async def test_normal_day_zero_entries_allowed():
    db = FakeDb()
    chip, _ = _seed_active(db, NORMAL)
    now = israel_at(NORMAL, 10)
    assert await _available(db, chip.id, now) is True
    await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a1")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_normal_day_one_entry_denied():
    db = FakeDb()
    chip, _ = _seed_active(db, NORMAL)
    now = israel_at(NORMAL, 16)
    _seed_used(db, chip.id, israel_at(NORMAL, 9), "used")
    assert await _available(db, chip.id, now) is False
    with pytest.raises(AppError) as exc:
        await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a2")  # type: ignore[arg-type]
    assert exc.value.code == "daily_limit_reached"


@pytest.mark.asyncio
async def test_friday_before_noon_one_entry_denied():
    db = FakeDb()
    chip, _ = _seed_active(db, FRIDAY)
    now = israel_at(FRIDAY, 11, 59, 59)
    assert await _available(db, chip.id, now) is True
    _seed_used(db, chip.id, israel_at(FRIDAY, 8), "used")
    assert await _available(db, chip.id, now) is False
    with pytest.raises(AppError) as exc:
        await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a2")  # type: ignore[arg-type]
    assert exc.value.code == "daily_limit_reached"


@pytest.mark.asyncio
async def test_friday_after_noon_second_entry_allowed_third_denied():
    db = FakeDb()
    chip, _ = _seed_active(db, FRIDAY)
    noon = israel_at(FRIDAY, 12)
    assert await _available(db, chip.id, noon) is True
    await mark_free_entry(db, member_id=chip.id, now=noon, idempotency_key="a1")  # type: ignore[arg-type]
    assert await _available(db, chip.id, israel_at(FRIDAY, 14)) is True
    await mark_free_entry(db, member_id=chip.id, now=israel_at(FRIDAY, 14), idempotency_key="a2")  # type: ignore[arg-type]
    assert await _available(db, chip.id, israel_at(FRIDAY, 15)) is False
    with pytest.raises(AppError) as exc:
        await mark_free_entry(db, member_id=chip.id, now=israel_at(FRIDAY, 15), idempotency_key="a3")  # type: ignore[arg-type]
    assert exc.value.code == "daily_limit_reached"


@pytest.mark.asyncio
async def test_erev_holiday_before_and_after_noon():
    db = FakeDb()
    chip, _ = _seed_active(db, EREV_PESACH)
    morning = israel_at(EREV_PESACH, 10)
    assert await _available(db, chip.id, morning) is True
    await mark_free_entry(db, member_id=chip.id, now=morning, idempotency_key="a1")  # type: ignore[arg-type]
    assert await _available(db, chip.id, israel_at(EREV_PESACH, 11, 30)) is False
    afternoon = israel_at(EREV_PESACH, 14)
    assert await _available(db, chip.id, afternoon) is True
    await mark_free_entry(db, member_id=chip.id, now=afternoon, idempotency_key="a2")  # type: ignore[arg-type]
    assert await _available(db, chip.id, israel_at(EREV_PESACH, 15)) is False


@pytest.mark.asyncio
async def test_erev_yom_kippur_after_noon_allows_two():
    db = FakeDb()
    chip, _ = _seed_active(db, EREV_YK)
    now = israel_at(EREV_YK, 14)
    await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a1")  # type: ignore[arg-type]
    await mark_free_entry(db, member_id=chip.id, now=israel_at(EREV_YK, 15), idempotency_key="a2")  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        await mark_free_entry(db, member_id=chip.id, now=israel_at(EREV_YK, 16), idempotency_key="a3")  # type: ignore[arg-type]
    assert exc.value.code == "daily_limit_reached"


@pytest.mark.asyncio
async def test_holiday_day_itself_is_single_entry():
    db = FakeDb()
    chip, _ = _seed_active(db, YK)
    now = israel_at(YK, 15)
    await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a1")  # type: ignore[arg-type]
    assert await _available(db, chip.id, now) is False


@pytest.mark.asyncio
async def test_saturday_is_single_entry():
    db = FakeDb()
    chip, _ = _seed_active(db, SATURDAY)
    now = israel_at(SATURDAY, 15)
    await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a1")  # type: ignore[arg-type]
    assert await _available(db, chip.id, now) is False


@pytest.mark.asyncio
async def test_chol_hamoed_is_single_entry():
    db = FakeDb()
    chip, _ = _seed_active(db, CHM_PESACH)
    now = israel_at(CHM_PESACH, 15)
    await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="a1")  # type: ignore[arg-type]
    assert await _available(db, chip.id, now) is False


@pytest.mark.asyncio
async def test_legacy_last_free_entry_on_counts_as_one():
    db = FakeDb()
    chip, _ = _seed_active(db, NORMAL, last_free=NORMAL)
    now = israel_at(NORMAL, 16)
    assert await _available(db, chip.id, now) is False


@pytest.mark.asyncio
async def test_membership_valid_in_hebrew_month_and_expires_next_month():
    db = FakeDb()
    chip = _chip()
    db.add(chip)
    av = hebrew_month_for(AV_5786)
    db.add(_sub(chip.id, year=av.year, month=av.month, name=av.name))
    snap_av = await subscription_snapshot(db, chip.id, now=israel_at(AV_5786, 10))  # type: ignore[arg-type]
    snap_tishrei = await subscription_snapshot(db, chip.id, now=israel_at(TISHREI_5787, 10))  # type: ignore[arg-type]
    assert snap_av.subscription_active is True
    assert snap_tishrei.subscription_active is False


@pytest.mark.asyncio
async def test_same_hebrew_month_name_different_year_is_separate():
    db = FakeDb()
    chip = _chip()
    db.add(chip)
    av_5787 = hebrew_month_for(AV_5787)
    db.add(_sub(chip.id, year=av_5787.year, month=av_5787.month, name=av_5787.name))
    snap_old = await subscription_snapshot(db, chip.id, now=israel_at(AV_5786, 10))  # type: ignore[arg-type]
    snap_new = await subscription_snapshot(db, chip.id, now=israel_at(AV_5787, 10))  # type: ignore[arg-type]
    assert snap_old.subscription_active is False
    assert snap_new.subscription_active is True
    assert snap_new.subscription_hebrew_year == 5787


@pytest.mark.asyncio
async def test_utc_midnight_counts_on_israel_date():
    db = FakeDb()
    chip, _ = _seed_active(db, SATURDAY)
    # 21:00 UTC Friday 14th == 00:00 Saturday in Israel (IDT).
    _seed_used(db, chip.id, datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc), "utc-sat")
    assert await _available(db, chip.id, israel_at(SATURDAY, 10)) is False
    assert await _available(db, chip.id, israel_at(FRIDAY, 23)) is True


@pytest.mark.asyncio
async def test_mark_retry_same_idempotency_key_does_not_consume_second_slot():
    db = FakeDb()
    chip, _ = _seed_active(db, FRIDAY)
    now = israel_at(FRIDAY, 14)
    first = await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="attempt-1")  # type: ignore[arg-type]
    second = await mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="attempt-1")  # type: ignore[arg-type]
    assert first.id == second.id
    free = [a for a in db.activities if a.event_type == EVENT_FREE_ENTRY]
    assert len(free) == 1
    assert await _available(db, chip.id, now) is True


@pytest.mark.asyncio
async def test_concurrent_marks_cannot_both_take_last_slot():
    db = FakeDb()
    chip, _ = _seed_active(db, FRIDAY)
    now = israel_at(FRIDAY, 14)
    _seed_used(db, chip.id, israel_at(FRIDAY, 8), "already")

    results = await asyncio.gather(
        mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="c1"),  # type: ignore[arg-type]
        mark_free_entry(db, member_id=chip.id, now=now, idempotency_key="c2"),  # type: ignore[arg-type]
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    denials = [r for r in results if isinstance(r, AppError) and r.code == "daily_limit_reached"]
    assert len(successes) == 1
    assert len(denials) == 1


@pytest.mark.asyncio
async def test_activate_is_idempotent_on_transaction_id():
    db = FakeDb()
    chip = _chip()
    db.add(chip)
    first = await activate_subscription(
        db,  # type: ignore[arg-type]
        member_id=chip.id,
        amount_cents=30000,
        nedarim_transaction_id="TXN-1",
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
    )
    second = await activate_subscription(
        db,  # type: ignore[arg-type]
        member_id=chip.id,
        amount_cents=30000,
        nedarim_transaction_id="TXN-1",
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
    )
    assert first.nedarim_transaction_id == second.nedarim_transaction_id
    with pytest.raises(AppError) as exc:
        await activate_subscription(
            db,  # type: ignore[arg-type]
            member_id=chip.id,
            amount_cents=30000,
            nedarim_transaction_id="TXN-2",
            hebrew_year=5786,
            hebrew_month=5,
            hebrew_month_name="אב",
        )
    assert exc.value.code == "subscription_already_active"
