from __future__ import annotations

import secrets
import time

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from .clients import ChipClient, HardwareClient
from .settings import settings

_mgmt_tokens: dict[str, float] = {}
TOKEN_TTL_SECONDS = 60 * 60 * 8  # 8 hours


class ManagementPinRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


class ManagementAuthResponse(BaseModel):
    token: str


class ChipTopupRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)
    amount_cents: int = Field(gt=0, le=1_000_000)


class ChipTopupResponse(BaseModel):
    uid: str
    chip_id: str
    balance_cents: int
    added_cents: int


class ChipInfoResponse(BaseModel):
    uid: str
    chip_id: str
    balance_cents: int
    is_enabled: bool


def _purge_expired_tokens() -> None:
    now = time.time()
    expired = [token for token, expires_at in _mgmt_tokens.items() if expires_at <= now]
    for token in expired:
        del _mgmt_tokens[token]


def _verify_pin(pin: str) -> None:
    if not settings.management_pin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="management_disabled")
    if pin != settings.management_pin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_pin")


def create_management_token() -> str:
    _purge_expired_tokens()
    token = secrets.token_urlsafe(32)
    _mgmt_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return token


def require_management_token(x_management_token: str | None = Header(default=None)) -> None:
    if not settings.management_pin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="management_disabled")
    if not x_management_token or x_management_token not in _mgmt_tokens:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    if _mgmt_tokens[x_management_token] <= time.time():
        del _mgmt_tokens[x_management_token]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")


async def authenticate_pin(req: ManagementPinRequest) -> ManagementAuthResponse:
    _verify_pin(req.pin)
    return ManagementAuthResponse(token=create_management_token())


async def get_chip_info(uid: str, chip_client: ChipClient) -> ChipInfoResponse:
    try:
        chip = await chip_client.validate(uid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found") from None
    return ChipInfoResponse(
        uid=chip.uid,
        chip_id=chip.chip_id,
        balance_cents=chip.balance_cents,
        is_enabled=chip.is_enabled,
    )


async def topup_chip(req: ChipTopupRequest, chip_client: ChipClient) -> ChipTopupResponse:
    try:
        chip = await chip_client.validate(req.uid)
    except ValueError:
        await chip_client.register(req.uid)
        chip = await chip_client.validate(req.uid)

    new_balance = await chip_client.adjust_balance(
        chip_id=chip.chip_id,
        delta_cents=req.amount_cents,
        reason="management_topup",
        description="management top-up",
    )
    return ChipTopupResponse(
        uid=chip.uid,
        chip_id=chip.chip_id,
        balance_cents=new_balance,
        added_cents=req.amount_cents,
    )


async def open_door(hardware_client: HardwareClient) -> None:
    await hardware_client.open_door(seconds=settings.door_unlock_seconds)
