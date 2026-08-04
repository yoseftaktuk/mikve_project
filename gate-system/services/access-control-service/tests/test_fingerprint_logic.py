from __future__ import annotations

import asyncio
import uuid

import pytest

from app.clients import ChipValidation
from app.fingerprint_logic import (
    PendingApprovalStore,
    PendingEnrollmentStore,
    approve_pending,
    complete_enrollment,
    process_fingerprint_scan,
    slot_to_uid,
    uid_to_slot,
)
from app.settings import settings

FEE = settings.entrance_fee_cents


def chip_id_for(uid: str) -> str:
    """Chip ids are UUIDs in chip-service, so fakes must look the same."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, uid))


class FakeDb:
    """Minimal AsyncSession stand-in: records added rows, no-op commit."""

    def __init__(self) -> None:
        self.rows: list[object] = []
        self.commits = 0
        self._by_id: dict = {}

    def add(self, row: object) -> None:
        self.rows.append(row)
        row_id = getattr(row, "id", None)
        if row_id is not None:
            self._by_id[row_id] = row

    async def flush(self) -> None:
        for row in self.rows:
            row_id = getattr(row, "id", None)
            if row_id is not None:
                self._by_id[row_id] = row

    async def commit(self) -> None:
        self.commits += 1
        await self.flush()

    async def get(self, model: type, key: object) -> object | None:
        return self._by_id.get(key)


class FakeChipClient:
    def __init__(self, chips: dict[str, ChipValidation] | None = None) -> None:
        self.chips = chips or {}
        self.balances: dict[str, int] = {c.chip_id: c.balance_cents for c in self.chips.values()}
        self.registered: list[tuple[str, str | None]] = []
        self.renamed: list[tuple[str, str | None]] = []
        self.adjustments: list[tuple[str, int, str]] = []

    async def validate(self, uid: str) -> ChipValidation:
        if uid not in self.chips:
            raise ValueError("chip_not_found")
        chip = self.chips[uid]
        return ChipValidation(
            chip_id=chip.chip_id,
            uid=chip.uid,
            is_enabled=chip.is_enabled,
            assigned_user_id=chip.assigned_user_id,
            balance_cents=self.balances[chip.chip_id],
            holder_name=chip.holder_name,
        )

    async def register(self, uid: str, holder_name: str | None = None) -> None:
        self.registered.append((uid, holder_name))
        chip_id = chip_id_for(uid)
        self.chips[uid] = ChipValidation(
            chip_id=chip_id,
            uid=uid,
            is_enabled=True,
            assigned_user_id=None,
            balance_cents=0,
            holder_name=holder_name,
        )
        self.balances[chip_id] = 0

    async def rename(self, chip_id: str, holder_name: str | None) -> None:
        self.renamed.append((chip_id, holder_name))

    async def adjust_balance(
        self,
        chip_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        if idempotency_key:
            for prev in self.adjustments:
                # previous tuples are (chip_id, delta, reason) — treat same key as no-op
                pass
            for key, bal in list(getattr(self, "_idempotency", {}).items()):
                if key == idempotency_key:
                    return bal
        new_balance = self.balances[chip_id] + delta_cents
        if new_balance < 0:
            raise ValueError("insufficient_balance")
        self.balances[chip_id] = new_balance
        self.adjustments.append((chip_id, delta_cents, reason))
        if idempotency_key:
            self._idempotency = getattr(self, "_idempotency", {})
            self._idempotency[idempotency_key] = new_balance
        return new_balance


class FakeHardwareClient:
    def __init__(self) -> None:
        self.door_opens = 0

    async def open_door(self, seconds: int, **kwargs) -> dict:
        self.door_opens += 1
        return {"status": "confirmed", "unlocked_for_seconds": seconds}


class Recorder:
    """Collects published events in order."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.get("type") for e in self.events]

    def last(self) -> dict:
        return self.events[-1]


def chip(uid: str, *, balance_cents: int, is_enabled: bool = True, holder_name: str | None = "דנה") -> ChipValidation:
    return ChipValidation(
        chip_id=chip_id_for(uid),
        uid=uid,
        is_enabled=is_enabled,
        assigned_user_id=None,
        balance_cents=balance_cents,
        holder_name=holder_name,
    )


def test_slot_uid_roundtrip():
    assert slot_to_uid(7) == "FP-007"
    assert uid_to_slot("FP-007") == 7
    assert uid_to_slot("FP-142") == 142


def test_uid_to_slot_ignores_other_uids():
    assert uid_to_slot("DEMO-UID-1234") is None
    assert uid_to_slot("FP-abc") is None


async def test_scan_publishes_pending_without_charging():
    uid = slot_to_uid(3)
    chip_client = FakeChipClient({uid: chip(uid, balance_cents=FEE * 2)})
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(publish)
    db = FakeDb()

    approval = await process_fingerprint_scan(
        3, db, chip_client=chip_client, publish=publish, approvals=approvals, confidence=90
    )

    assert approval is not None
    assert publish.types() == ["access.pending"]
    assert publish.last()["holder_name"] == "דנה"
    assert publish.last()["fee_cents"] == FEE
    assert chip_client.adjustments == []


@pytest.mark.parametrize(
    ("chips", "reason"),
    [
        ({}, "unknown_fingerprint"),
        ({slot_to_uid(5): chip(slot_to_uid(5), balance_cents=FEE, is_enabled=False)}, "chip_disabled"),
    ],
)
async def test_scan_denials(chips: dict, reason: str):
    chip_client = FakeChipClient(chips)
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(publish)
    db = FakeDb()

    approval = await process_fingerprint_scan(
        5, db, chip_client=chip_client, publish=publish, approvals=approvals
    )

    assert approval is None
    assert publish.types() == ["access.denied"]
    assert publish.last()["reason"] == reason
    assert publish.last()["method"] == "fingerprint"
    assert approvals.current is None
    assert db.commits == 1


async def test_scan_insufficient_balance_offers_topup():
    uid = slot_to_uid(5)
    chip_client = FakeChipClient({uid: chip(uid, balance_cents=FEE - 1)})
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(publish)
    db = FakeDb()

    approval = await process_fingerprint_scan(
        5, db, chip_client=chip_client, publish=publish, approvals=approvals
    )

    assert approval is None
    assert publish.types() == ["access.topup_needed"]
    assert publish.last()["uid"] == uid
    assert publish.last()["balance_cents"] == FEE - 1
    assert publish.last()["fee_cents"] == FEE
    assert approvals.current is None
    assert db.commits == 1


async def test_approve_charges_once_and_opens_door():
    from tests.test_access_saga import _patch_repo

    uid = slot_to_uid(9)
    chip_client = FakeChipClient({uid: chip(uid, balance_cents=FEE * 3)})
    hardware_client = FakeHardwareClient()
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(publish)
    db = FakeDb()

    approval = await process_fingerprint_scan(
        9, db, chip_client=chip_client, publish=publish, approvals=approvals
    )
    assert approval is not None

    patches = _patch_repo()
    for p in patches:
        p.start()
    try:
        decision = await approve_pending(
            approval.approval_id,
            db,
            chip_client=chip_client,
            hardware_client=hardware_client,
            publish=publish,
            approvals=approvals,
        )
    finally:
        for p in patches:
            p.stop()

    assert decision.granted is True
    assert hardware_client.door_opens == 1
    assert chip_client.adjustments == [(chip_id_for(uid), -FEE, "entry_fee")]
    assert publish.types() == ["access.pending", "access.granted"]

    # A second confirmation of the same scan must not charge again.
    with pytest.raises(ValueError, match="approval_not_found"):
        await approve_pending(
            approval.approval_id,
            db,
            chip_client=chip_client,
            hardware_client=hardware_client,
            publish=publish,
            approvals=approvals,
        )
    assert chip_client.adjustments == [(chip_id_for(uid), -FEE, "entry_fee")]
    assert hardware_client.door_opens == 1


async def test_approval_expires_and_publishes_cleared():
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=1)
    approvals.set_publish(publish)

    approval = await approvals.create(
        uid=slot_to_uid(1), chip_id=chip_id_for("FP-001"), holder_name="דנה", balance_cents=FEE, fee_cents=FEE
    )
    await asyncio.sleep(1.1)

    assert approvals.current is None
    assert publish.types() == ["access.pending_cleared"]
    assert publish.last()["reason"] == "timeout"

    with pytest.raises(ValueError, match="approval_not_found"):
        await approvals.consume(approval.approval_id)


async def test_new_scan_replaces_previous_approval():
    publish = Recorder()
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(publish)

    first = await approvals.create(
        uid=slot_to_uid(1), chip_id=chip_id_for("FP-001"), holder_name="א", balance_cents=FEE, fee_cents=FEE
    )
    second = await approvals.create(
        uid=slot_to_uid(2), chip_id=chip_id_for("FP-002"), holder_name="ב", balance_cents=FEE, fee_cents=FEE
    )

    assert publish.types() == ["access.pending_cleared"]
    assert publish.last()["reason"] == "replaced"
    with pytest.raises(ValueError, match="approval_not_found"):
        await approvals.consume(first.approval_id)
    assert (await approvals.consume(second.approval_id)).uid == slot_to_uid(2)


async def test_cancel_clears_only_matching_approval():
    approvals = PendingApprovalStore(timeout_seconds=25)
    approvals.set_publish(Recorder())
    approval = await approvals.create(
        uid=slot_to_uid(4), chip_id=chip_id_for("FP-004"), holder_name=None, balance_cents=FEE, fee_cents=FEE
    )

    assert await approvals.clear("some-other-id", reason="cancelled") is False
    assert approvals.current is not None
    assert await approvals.clear(approval.approval_id, reason="cancelled") is True
    assert approvals.current is None


async def test_complete_enrollment_creates_named_chip_with_initial_balance():
    chip_client = FakeChipClient()
    publish = Recorder()
    enrollments = PendingEnrollmentStore()
    session = enrollments.create(holder_name="דנה כהן", initial_amount_cents=5000)

    await complete_enrollment(
        session.session_id, 12, chip_client=chip_client, publish=publish, enrollments=enrollments
    )

    assert chip_client.registered == [("FP-012", "דנה כהן")]
    assert chip_client.balances[chip_id_for("FP-012")] == 5000
    assert publish.types() == ["fingerprint.registered"]
    assert publish.last()["uid"] == "FP-012"
    assert publish.last()["balance_cents"] == 5000


async def test_complete_enrollment_renames_when_sensor_reuses_slot():
    uid = slot_to_uid(2)
    chip_client = FakeChipClient({uid: chip(uid, balance_cents=1200, holder_name="שם קודם")})
    publish = Recorder()
    enrollments = PendingEnrollmentStore()
    session = enrollments.create(holder_name="שם חדש")

    await complete_enrollment(
        session.session_id, 2, chip_client=chip_client, publish=publish, enrollments=enrollments
    )

    assert chip_client.registered == []
    assert chip_client.renamed == [(chip_id_for(uid), "שם חדש")]
    assert publish.last()["balance_cents"] == 1200


async def test_complete_enrollment_ignores_unknown_session():
    chip_client = FakeChipClient()
    publish = Recorder()
    enrollments = PendingEnrollmentStore()

    await complete_enrollment(
        "missing-session", 3, chip_client=chip_client, publish=publish, enrollments=enrollments
    )

    assert chip_client.registered == []
    assert publish.events == []


def test_enrollment_session_is_consumed_once():
    enrollments = PendingEnrollmentStore()
    session = enrollments.create(holder_name="דנה")

    assert enrollments.pop(session.session_id) is not None
    assert enrollments.pop(session.session_id) is None
