from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from app.models import STATUS_SUB_ACTIVE, Chip, MonthlySubscription
from app.subscription_logic import activate_subscription, mark_free_entry, subscription_snapshot
from gate_shared.errors import AppError


class FakeResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one(self) -> object:
        assert self._value is not None
        return self._value

    def scalar(self) -> object | None:
        return self._value


class FakeDb:
    def __init__(self) -> None:
        self.chips: dict[uuid.UUID, Chip] = {}
        self.subs: list[MonthlySubscription] = []
        self.added: list[object] = []
        self.commits = 0

    def add(self, row: object) -> None:
        self.added.append(row)
        if isinstance(row, MonthlySubscription):
            self.subs.append(row)

    async def get(self, model: type, key: object) -> object | None:
        if model is Chip:
            return self.chips.get(key)  # type: ignore[arg-type]
        return None

    async def scalar(self, statement: object) -> object | None:
        # Very small stand-in: inspect compiled where criteria via string.
        text = str(statement)
        if "nedarim_transaction_id" in text:
            for sub in self.subs:
                if f"'{sub.nedarim_transaction_id}'" in text or sub.nedarim_transaction_id in text:
                    return sub
            # Fallback: match last activated by scanning added for equality checks isn't reliable;
            # use direct lookup helpers in tests instead.
        for sub in self.subs:
            if sub.status == STATUS_SUB_ACTIVE:
                return sub
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def refresh(self, row: object) -> None:
        return None


@pytest.mark.asyncio
async def test_activate_subscription_creates_row(monkeypatch: pytest.MonkeyPatch) -> None:
    chip_id = uuid.uuid4()
    db = FakeDb()
    db.chips[chip_id] = Chip(id=chip_id, uid="FP-001", is_enabled=True)

    monkeypatch.setattr(
        "app.subscription_logic.hebrew_month_for",
        lambda day=None: type("M", (), {"year": 5786, "month": 5, "name": "אב"})(),
    )

    # Patch select path: first lookup by txn None, then by month None, then after add return row.
    calls = {"n": 0}

    async def fake_scalar(statement: object) -> object | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # existing txn
        if calls["n"] == 2:
            return None  # existing month
        return db.subs[-1] if db.subs else None

    db.scalar = fake_scalar  # type: ignore[method-assign]

    row = await activate_subscription(
        db,  # type: ignore[arg-type]
        chip_id=chip_id,
        amount_cents=30000,
        nedarim_transaction_id="TX-1",
    )
    assert row.hebrew_year == 5786
    assert row.hebrew_month == 5
    assert row.hebrew_month_name == "אב"
    assert row.nedarim_transaction_id == "TX-1"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_activate_idempotent_on_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    chip_id = uuid.uuid4()
    db = FakeDb()
    db.chips[chip_id] = Chip(id=chip_id, uid="FP-001", is_enabled=True)
    existing = MonthlySubscription(
        id=uuid.uuid4(),
        chip_id=chip_id,
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
        amount_cents=30000,
        nedarim_transaction_id="TX-1",
        status=STATUS_SUB_ACTIVE,
        purchased_at=datetime.now(timezone.utc),
    )
    db.subs.append(existing)

    async def fake_scalar(statement: object) -> object | None:
        return existing

    db.scalar = fake_scalar  # type: ignore[method-assign]

    row = await activate_subscription(
        db,  # type: ignore[arg-type]
        chip_id=chip_id,
        amount_cents=30000,
        nedarim_transaction_id="TX-1",
    )
    assert row is existing
    assert db.commits == 0


@pytest.mark.asyncio
async def test_mark_free_entry_once_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    chip_id = uuid.uuid4()
    today = date(2026, 8, 6)
    sub = MonthlySubscription(
        id=uuid.uuid4(),
        chip_id=chip_id,
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
        amount_cents=30000,
        nedarim_transaction_id="TX-2",
        status=STATUS_SUB_ACTIVE,
        purchased_at=datetime.now(timezone.utc),
        last_free_entry_on=None,
    )
    db = FakeDb()
    db.subs.append(sub)
    monkeypatch.setattr("app.subscription_logic.israel_today", lambda: today)
    monkeypatch.setattr(
        "app.subscription_logic.hebrew_month_for",
        lambda day=None: type("M", (), {"year": 5786, "month": 5, "name": "אב"})(),
    )

    async def fake_scalar(statement: object) -> object | None:
        return sub

    db.scalar = fake_scalar  # type: ignore[method-assign]

    first = await mark_free_entry(db, chip_id=chip_id)  # type: ignore[arg-type]
    assert first.last_free_entry_on == today
    assert db.commits == 1

    second = await mark_free_entry(db, chip_id=chip_id)  # type: ignore[arg-type]
    assert second.last_free_entry_on == today
    assert db.commits == 1  # idempotent same day


@pytest.mark.asyncio
async def test_subscription_snapshot_free_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    chip_id = uuid.uuid4()
    today = date(2026, 8, 6)
    sub = MonthlySubscription(
        id=uuid.uuid4(),
        chip_id=chip_id,
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
        amount_cents=30000,
        nedarim_transaction_id="TX-3",
        status=STATUS_SUB_ACTIVE,
        purchased_at=datetime.now(timezone.utc),
        last_free_entry_on=None,
    )
    db = FakeDb()
    db.subs.append(sub)
    monkeypatch.setattr("app.subscription_logic.israel_today", lambda: today)
    monkeypatch.setattr(
        "app.subscription_logic.hebrew_month_for",
        lambda day=None: type("M", (), {"year": 5786, "month": 5, "name": "אב"})(),
    )

    async def fake_scalar(statement: object) -> object | None:
        return sub

    db.scalar = fake_scalar  # type: ignore[method-assign]

    snap = await subscription_snapshot(db, chip_id)  # type: ignore[arg-type]
    assert snap.subscription_active is True
    assert snap.subscription_free_entry_available_today is True

    sub.last_free_entry_on = today
    snap2 = await subscription_snapshot(db, chip_id)  # type: ignore[arg-type]
    assert snap2.subscription_free_entry_available_today is False


@pytest.mark.asyncio
async def test_activate_missing_chip() -> None:
    db = FakeDb()
    with pytest.raises(AppError) as exc:
        await activate_subscription(
            db,  # type: ignore[arg-type]
            chip_id=uuid.uuid4(),
            amount_cents=30000,
            nedarim_transaction_id="TX",
        )
    assert exc.value.code == "chip_not_found"


@pytest.mark.asyncio
async def test_activate_rejects_second_purchase_same_month(monkeypatch: pytest.MonkeyPatch) -> None:
    chip_id = uuid.uuid4()
    db = FakeDb()
    db.chips[chip_id] = Chip(id=chip_id, uid="FP-001", is_enabled=True)
    existing = MonthlySubscription(
        id=uuid.uuid4(),
        chip_id=chip_id,
        hebrew_year=5786,
        hebrew_month=5,
        hebrew_month_name="אב",
        amount_cents=30000,
        nedarim_transaction_id="TX-OLD",
        status=STATUS_SUB_ACTIVE,
        purchased_at=datetime.now(timezone.utc),
    )
    db.subs.append(existing)
    monkeypatch.setattr(
        "app.subscription_logic.hebrew_month_for",
        lambda day=None: type("M", (), {"year": 5786, "month": 5, "name": "אב"})(),
    )

    calls = {"n": 0}

    async def fake_scalar(statement: object) -> object | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # no matching transaction id
        return existing  # active month already exists

    db.scalar = fake_scalar  # type: ignore[method-assign]

    with pytest.raises(AppError) as exc:
        await activate_subscription(
            db,  # type: ignore[arg-type]
            chip_id=chip_id,
            amount_cents=30000,
            nedarim_transaction_id="TX-NEW",
        )
    assert exc.value.code == "subscription_already_active"
    assert db.commits == 0
