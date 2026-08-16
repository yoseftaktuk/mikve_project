"""Unit tests for the access-attempt saga."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.access_logic import CashSession
from app.saga.models import AccessAttempt, CashReceipt, DoorOperation, PaymentTransaction, RefundTransaction
from app.saga.orchestrator import AccessOrchestrator
from app.saga.repository import AccessAttemptRepository, TransitionConflictError
from app.saga.statuses import (
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_VALIDATED,
    VALID_TRANSITIONS,
)
from app.settings import settings


@dataclass
class FakeChip:
    chip_id: str
    uid: str
    balance_cents: int
    is_enabled: bool = True
    holder_name: str | None = "Test"
    assigned_user_id: str | None = None


class FakeDb:
    def __init__(self) -> None:
        self.rows: list[object] = []
        self.commits = 0
        self._by_id: dict[uuid.UUID, object] = {}

    def add(self, row: object) -> None:
        self.rows.append(row)
        row_id = getattr(row, "id", None)
        if isinstance(row_id, uuid.UUID):
            self._by_id[row_id] = row

    async def flush(self) -> None:
        for row in self.rows:
            row_id = getattr(row, "id", None)
            if isinstance(row_id, uuid.UUID):
                self._by_id[row_id] = row

    async def commit(self) -> None:
        self.commits += 1
        await self.flush()


class FakeFingerprintsClient:
    def __init__(self, chips: dict[str, FakeChip]) -> None:
        self.chips = chips
        self.adjusts: list[dict] = []
        self.free_entries: list[str] = []
        self.last_free_entry_key: str | None = None
        self.fail_free_entry: str | None = None

    async def validate(self, uid: str) -> FakeChip:
        chip = self.chips.get(uid)
        if chip is None:
            raise ValueError("chip_not_found")
        return chip

    async def adjust_balance(
        self,
        chip_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        if idempotency_key:
            for prev in self.adjusts:
                if prev.get("idempotency_key") == idempotency_key:
                    return next(c.balance_cents for c in self.chips.values() if c.chip_id == chip_id)
        chip = next(c for c in self.chips.values() if c.chip_id == chip_id)
        new_bal = chip.balance_cents + delta_cents
        if new_bal < 0:
            raise ValueError("insufficient_balance")
        chip.balance_cents = new_bal
        self.adjusts.append({"delta_cents": delta_cents, "idempotency_key": idempotency_key})
        return chip.balance_cents

    async def mark_subscription_free_entry(
        self, chip_id: str, *, idempotency_key: str | None = None
    ) -> None:
        self.last_free_entry_key = idempotency_key
        if self.fail_free_entry:
            raise ValueError(self.fail_free_entry)
        self.free_entries.append(chip_id)


class FakeHardwareClient:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def open_door(self, seconds: int, **kwargs) -> dict:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TimeoutError("door timeout")
        return {"status": "confirmed", "unlocked_for_seconds": seconds}


def _patch_repo():
    async def fake_create(self, attempt):
        self._db.add(attempt)
        await self._db.flush()
        return attempt

    async def fake_get(self, attempt_id):
        return self._db._by_id.get(attempt_id)

    async def fake_transition(self, attempt_id, expected_from, to, reason, **kwargs):
        if (expected_from, to) not in VALID_TRANSITIONS:
            raise ValueError(f"invalid_transition:{expected_from}->{to}")
        attempt = self._db._by_id[attempt_id]
        assert isinstance(attempt, AccessAttempt)
        if attempt.status != expected_from:
            raise TransitionConflictError("cas")
        attempt.status = to
        for key, value in kwargs.items():
            if value is not None and hasattr(attempt, key):
                setattr(attempt, key, value)
        return attempt

    async def fake_get_payment(self, key):
        for row in reversed(self._db.rows):
            if isinstance(row, PaymentTransaction) and row.idempotency_key == key:
                return row
        return None

    async def fake_get_refund(self, key):
        for row in reversed(self._db.rows):
            if isinstance(row, RefundTransaction) and row.idempotency_key == key:
                return row
        return None

    async def fake_add_payment(self, row):
        self._db.add(row)

    async def fake_add_refund(self, row):
        self._db.add(row)

    async def fake_add_door(self, row):
        self._db.add(row)
        await self._db.flush()

    async def fake_finish_door(self, operation_id, status, error_code=None):
        op = self._db._by_id.get(operation_id)
        if isinstance(op, DoorOperation):
            op.status = status
            op.error_code = error_code

    async def fake_get_receipt(self, attempt_id):
        for row in self._db.rows:
            if isinstance(row, CashReceipt) and row.attempt_id == attempt_id:
                return row
        return None

    async def fake_add_receipt(self, row):
        self._db.add(row)
        await self._db.flush()
        return row

    return [
        patch.object(AccessAttemptRepository, "create", fake_create),
        patch.object(AccessAttemptRepository, "get", fake_get),
        patch.object(AccessAttemptRepository, "transition", fake_transition),
        patch.object(AccessAttemptRepository, "get_payment_by_key", fake_get_payment),
        patch.object(AccessAttemptRepository, "get_refund_by_key", fake_get_refund),
        patch.object(AccessAttemptRepository, "add_payment", fake_add_payment),
        patch.object(AccessAttemptRepository, "add_refund", fake_add_refund),
        patch.object(AccessAttemptRepository, "add_door_operation", fake_add_door),
        patch.object(AccessAttemptRepository, "finish_door_operation", fake_finish_door),
        patch.object(AccessAttemptRepository, "get_receipt_for_attempt", fake_get_receipt),
        patch.object(AccessAttemptRepository, "add_receipt", fake_add_receipt),
        patch.object(AccessAttemptRepository, "get_by_hardware_event_id", AsyncMock(return_value=None)),
    ]


def test_valid_transitions_include_happy_path():
    assert (STATUS_CREATED, STATUS_VALIDATED) in VALID_TRANSITIONS
    assert ("VALIDATED", "CHARGED") in VALID_TRANSITIONS
    assert ("CHARGED", "DOOR_OPENING") in VALID_TRANSITIONS
    assert ("DOOR_OPENING", STATUS_COMPLETED) in VALID_TRANSITIONS
    assert (STATUS_CREATED, STATUS_COMPLETED) not in VALID_TRANSITIONS


@pytest.mark.asyncio
async def test_cash_try_pay_is_atomic_and_idempotent():
    cash = CashSession(timeout_seconds=20)
    await cash.add(500)
    first = await cash.try_pay(fee_cents=500, attempt_id="a1")
    second = await cash.try_pay(fee_cents=500, attempt_id="a1")
    third = await cash.try_pay(fee_cents=500, attempt_id="a2")
    assert first.ok and second.ok
    assert first.paid_total_cents == 500
    assert not third.ok
    assert await cash.restore_fee("a1") is True
    assert cash.accumulated_cents == 500


@pytest.mark.asyncio
async def test_fingerprint_happy_path_charges_with_idempotency_key():
    fee = settings.entrance_fee_cents
    chip_id = str(uuid.uuid4())
    chips = {"FP-001": FakeChip(chip_id=chip_id, uid="FP-001", balance_cents=fee * 3)}
    chip_client = FakeFingerprintsClient(chips)
    events: list[dict] = []
    db = FakeDb()
    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        orch = AccessOrchestrator(
            chip_client=chip_client,  # type: ignore[arg-type]
            hardware_client=FakeHardwareClient(),  # type: ignore[arg-type]
            cash_session=CashSession(timeout_seconds=20),
            publish=events.append,
        )
        result = await orch.run_fingerprint_approve(
            approval_id="appr-1",
            uid="FP-001",
            chip_id=chip_id,
            holder_name="Test",
            balance_cents=fee * 3,
            fee_cents=fee,
            db=db,  # type: ignore[arg-type]
        )
        assert result.granted is True
        assert chips["FP-001"].balance_cents == fee * 2
        assert chip_client.adjusts[0]["idempotency_key"].startswith("access-charge:")
        assert any(e.get("type") == "access.granted" for e in events)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_subscription_zero_fee_skips_balance_charge():
    chip_id = str(uuid.uuid4())
    chips = {"FP-001": FakeChip(chip_id=chip_id, uid="FP-001", balance_cents=0)}
    chip_client = FakeFingerprintsClient(chips)
    events: list[dict] = []
    db = FakeDb()
    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        orch = AccessOrchestrator(
            chip_client=chip_client,  # type: ignore[arg-type]
            hardware_client=FakeHardwareClient(),  # type: ignore[arg-type]
            cash_session=CashSession(timeout_seconds=20),
            publish=events.append,
        )
        result = await orch.run_fingerprint_approve(
            approval_id="appr-sub",
            uid="FP-001",
            chip_id=chip_id,
            holder_name="Test",
            balance_cents=0,
            fee_cents=0,
            db=db,  # type: ignore[arg-type]
        )
        assert result.granted is True
        assert chips["FP-001"].balance_cents == 0
        assert chip_client.adjusts == []
        assert chip_client.free_entries == [chip_id]
        assert chip_client.last_free_entry_key
        granted = next(e for e in events if e.get("type") == "access.granted")
        assert granted["method"] == "subscription"
        assert granted["fee_cents"] == 0
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_subscription_daily_limit_denies_without_door():
    chip_id = str(uuid.uuid4())
    chips = {"FP-001": FakeChip(chip_id=chip_id, uid="FP-001", balance_cents=0)}
    chip_client = FakeFingerprintsClient(chips)
    chip_client.fail_free_entry = "daily_limit_reached"
    events: list[dict] = []
    db = FakeDb()
    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        orch = AccessOrchestrator(
            chip_client=chip_client,  # type: ignore[arg-type]
            hardware_client=FakeHardwareClient(),  # type: ignore[arg-type]
            cash_session=CashSession(timeout_seconds=20),
            publish=events.append,
        )
        result = await orch.run_fingerprint_approve(
            approval_id="appr-limit",
            uid="FP-001",
            chip_id=chip_id,
            holder_name="Test",
            balance_cents=0,
            fee_cents=0,
            db=db,  # type: ignore[arg-type]
        )
        assert result.granted is False
        assert result.reason == "daily_limit_reached"
        assert chip_client.free_entries == []
        assert any(e.get("type") == "access.denied" and e.get("reason") == "daily_limit_reached" for e in events)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_fingerprint_door_failure_refunds_balance():
    fee = settings.entrance_fee_cents
    chip_id = str(uuid.uuid4())
    chips = {"FP-001": FakeChip(chip_id=chip_id, uid="FP-001", balance_cents=fee * 2)}
    chip_client = FakeFingerprintsClient(chips)
    events: list[dict] = []
    db = FakeDb()
    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        orch = AccessOrchestrator(
            chip_client=chip_client,  # type: ignore[arg-type]
            hardware_client=FakeHardwareClient(fail_times=10),  # type: ignore[arg-type]
            cash_session=CashSession(timeout_seconds=20),
            publish=events.append,
        )
        result = await orch.run_fingerprint_approve(
            approval_id="appr-2",
            uid="FP-001",
            chip_id=chip_id,
            holder_name="Test",
            balance_cents=fee * 2,
            fee_cents=fee,
            db=db,  # type: ignore[arg-type]
        )
        assert result.granted is False
        assert chips["FP-001"].balance_cents == fee * 2
        assert any(e.get("alert") == "DoorFailedAfterCharge" for e in events)
        assert any(e.get("type") == "access.refunded" for e in events)
        assert any(a["delta_cents"] > 0 for a in chip_client.adjusts)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_cash_door_failure_issues_receipt():
    fee = settings.entrance_fee_cents
    events: list[dict] = []
    db = FakeDb()
    cash = CashSession(timeout_seconds=20)
    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        orch = AccessOrchestrator(
            chip_client=FakeFingerprintsClient({}),  # type: ignore[arg-type]
            hardware_client=FakeHardwareClient(fail_times=10),  # type: ignore[arg-type]
            cash_session=cash,
            publish=events.append,
        )
        granted, _ = await orch.run_cash_inserted(fee, db)  # type: ignore[arg-type]
        assert granted is False
        assert any(e.get("alert") == "CashReceiptIssued" for e in events)
        assert cash.accumulated_cents == fee
        assert any(isinstance(r, CashReceipt) for r in db.rows)
    finally:
        for p in patches:
            p.stop()
