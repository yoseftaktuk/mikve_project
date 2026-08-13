"""Unit tests for management registered-user list/update/delete."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException

from app.clients import HardwareUnavailableError, LedgerUser
from app.management import (
    ManagementUserUpdateRequest,
    delete_user,
    list_users,
    update_user,
)


@dataclass
class FakeFingerprintsClient:
    users: dict[str, LedgerUser] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    adjusts: list[dict] = field(default_factory=list)

    async def list_users(self) -> list[LedgerUser]:
        return list(self.users.values())

    async def update_user(
        self,
        chip_id: str,
        *,
        holder_name: str | None = None,
        national_id: str | None = None,
        is_enabled: bool | None = None,
        set_holder_name: bool = False,
        set_national_id: bool = False,
        set_is_enabled: bool = False,
    ) -> LedgerUser:
        user = self.users.get(chip_id)
        if user is None:
            raise ValueError("chip_not_found")
        updated = LedgerUser(
            chip_id=user.chip_id,
            uid=user.uid,
            holder_name=holder_name if set_holder_name else user.holder_name,
            national_id=national_id if set_national_id else user.national_id,
            is_enabled=is_enabled if set_is_enabled and is_enabled is not None else user.is_enabled,
            balance_cents=user.balance_cents,
            created_at=user.created_at,
        )
        self.users[chip_id] = updated
        return updated

    async def get_by_id(self, chip_id: str) -> LedgerUser | None:
        return self.users.get(chip_id)

    async def adjust_balance(
        self,
        chip_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        user = self.users.get(chip_id)
        if user is None:
            raise ValueError("chip_not_found")
        new_bal = user.balance_cents + delta_cents
        if new_bal < 0:
            raise ValueError("insufficient_balance")
        updated = LedgerUser(
            chip_id=user.chip_id,
            uid=user.uid,
            holder_name=user.holder_name,
            national_id=user.national_id,
            is_enabled=user.is_enabled,
            balance_cents=new_bal,
            created_at=user.created_at,
        )
        self.users[chip_id] = updated
        self.adjusts.append({"delta_cents": delta_cents, "reason": reason})
        return new_bal

    async def delete_user(self, chip_id: str) -> None:
        if chip_id not in self.users:
            raise ValueError("chip_not_found")
        del self.users[chip_id]
        self.deleted.append(chip_id)


@dataclass
class FakeHardwareClient:
    deleted_slots: list[int] = field(default_factory=list)
    fail: bool = False

    async def delete_fingerprint(self, slot: int) -> None:
        if self.fail:
            raise HardwareUnavailableError("down")
        self.deleted_slots.append(slot)


@pytest.mark.asyncio
async def test_list_users_returns_ledger_rows():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-001",
                holder_name="דנה",
                is_enabled=True,
                balance_cents=500,
            )
        }
    )
    rows = await list_users(client)  # type: ignore[arg-type]
    assert len(rows) == 1
    assert rows[0].uid == "FP-001"
    assert rows[0].holder_name == "דנה"


@pytest.mark.asyncio
async def test_update_user_renames_and_disables():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-002",
                holder_name="ישן",
                is_enabled=True,
                balance_cents=100,
            )
        }
    )
    updated = await update_user(
        "c1",
        ManagementUserUpdateRequest(holder_name="חדש", is_enabled=False),
        client,  # type: ignore[arg-type]
    )
    assert updated.holder_name == "חדש"
    assert updated.is_enabled is False


@pytest.mark.asyncio
async def test_update_user_sets_absolute_balance_increase():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-010",
                holder_name="א",
                is_enabled=True,
                balance_cents=500,
            )
        }
    )
    updated = await update_user(
        "c1",
        ManagementUserUpdateRequest(balance_cents=2000),
        client,  # type: ignore[arg-type]
    )
    assert updated.balance_cents == 2000
    assert client.adjusts == [{"delta_cents": 1500, "reason": "management_set_balance"}]


@pytest.mark.asyncio
async def test_update_user_sets_absolute_balance_decrease():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-011",
                holder_name="ב",
                is_enabled=True,
                balance_cents=2000,
            )
        }
    )
    updated = await update_user(
        "c1",
        ManagementUserUpdateRequest(balance_cents=500),
        client,  # type: ignore[arg-type]
    )
    assert updated.balance_cents == 500
    assert client.adjusts == [{"delta_cents": -1500, "reason": "management_set_balance"}]


@pytest.mark.asyncio
async def test_update_user_skips_adjust_when_balance_unchanged():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-012",
                holder_name="ג",
                is_enabled=True,
                balance_cents=1500,
            )
        }
    )
    updated = await update_user(
        "c1",
        ManagementUserUpdateRequest(balance_cents=1500),
        client,  # type: ignore[arg-type]
    )
    assert updated.balance_cents == 1500
    assert client.adjusts == []


@pytest.mark.asyncio
async def test_delete_user_clears_fingerprint_slot_for_fp_uid():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-007",
                holder_name="מחיקה",
                is_enabled=True,
                balance_cents=0,
            )
        }
    )
    hardware = FakeHardwareClient()
    await delete_user("c1", client, hardware)  # type: ignore[arg-type]
    assert client.deleted == ["c1"]
    assert hardware.deleted_slots == [7]


@pytest.mark.asyncio
async def test_delete_user_skips_hardware_for_non_fp_uid():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="LEGACY-UID",
                holder_name="ללא אצבע",
                is_enabled=True,
                balance_cents=0,
            )
        }
    )
    hardware = FakeHardwareClient()
    await delete_user("c1", client, hardware)  # type: ignore[arg-type]
    assert client.deleted == ["c1"]
    assert hardware.deleted_slots == []


@pytest.mark.asyncio
async def test_delete_user_continues_when_hardware_unavailable():
    client = FakeFingerprintsClient(
        users={
            "c1": LedgerUser(
                chip_id="c1",
                uid="FP-003",
                holder_name="א",
                is_enabled=True,
                balance_cents=0,
            )
        }
    )
    hardware = FakeHardwareClient(fail=True)
    await delete_user("c1", client, hardware)  # type: ignore[arg-type]
    assert client.deleted == ["c1"]


@pytest.mark.asyncio
async def test_delete_missing_user_returns_404():
    with pytest.raises(HTTPException) as exc:
        await delete_user("missing", FakeFingerprintsClient(), FakeHardwareClient())  # type: ignore[arg-type]
    assert exc.value.status_code == 404
