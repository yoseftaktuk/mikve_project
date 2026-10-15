"""Tests for management registered-user API models."""

import pytest
from fastapi import HTTPException

from app.clients import LedgerUser
from app.management import ManagementUserResponse, ManagementUserUpdateRequest, update_user


def test_management_user_response_uses_fingerprint_id() -> None:
    body = ManagementUserResponse(
        fingerprint_id="fp-1",
        uid="FP-001",
        holder_name="Dana",
        is_enabled=True,
        balance_cents=100,
    ).model_dump()
    assert body["fingerprint_id"] == "fp-1"
    assert "chip_id" not in body


class _MissingBalanceClient:
    def __init__(self) -> None:
        self.adjust_calls = 0

    async def get_by_id(self, member_id: str) -> LedgerUser:
        return LedgerUser(
            member_id=member_id,
            uid="FP-001",
            holder_name="Dana",
            is_enabled=True,
            balance_cents=None,
        )

    async def adjust_balance(
        self,
        member_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        self.adjust_calls += 1
        return 0


async def test_balance_update_does_not_adjust_when_balance_is_unknown():
    client = _MissingBalanceClient()
    with pytest.raises(HTTPException) as exc:
        await update_user("member-1", ManagementUserUpdateRequest(balance_cents=500), client)  # type: ignore[arg-type]
    assert exc.value.status_code == 503
    assert exc.value.detail == "balance_unavailable"
    assert client.adjust_calls == 0
