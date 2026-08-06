from __future__ import annotations

import logging
import secrets
import time

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .clients import FingerprintsClient, HardwareClient, HardwareUnavailableError
from .fingerprint_logic import uid_to_slot
from .settings import settings

logger = logging.getLogger(__name__)

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
    holder_name: str | None = None


class ManagementUserResponse(BaseModel):
    chip_id: str
    uid: str
    holder_name: str | None = None
    is_enabled: bool
    balance_cents: int
    created_at: str | None = None


class ManagementUserUpdateRequest(BaseModel):
    holder_name: str | None = Field(default=None, max_length=80)
    is_enabled: bool | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> ManagementUserUpdateRequest:
        if "holder_name" not in self.model_fields_set and "is_enabled" not in self.model_fields_set:
            raise ValueError("Provide holder_name and/or is_enabled")
        return self


def _purge_expired_tokens() -> None:
    """Remove expired management session tokens from memory."""
    now = time.time()
    expired = [token for token, expires_at in _mgmt_tokens.items() if expires_at <= now]
    for token in expired:
        del _mgmt_tokens[token]


def _verify_pin(pin: str) -> None:
    """Raise if the provided management PIN is missing or invalid."""
    if not settings.management_pin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="management_disabled")
    if pin != settings.management_pin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_pin")


def create_management_token() -> str:
    """Create a short-lived management session token."""
    _purge_expired_tokens()
    token = secrets.token_urlsafe(32)
    _mgmt_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return token


def require_management_token(x_management_token: str | None = Header(default=None)) -> None:
    """FastAPI dependency that requires a valid management token header."""
    if not settings.management_pin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="management_disabled")
    if not x_management_token or x_management_token not in _mgmt_tokens:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    if _mgmt_tokens[x_management_token] <= time.time():
        del _mgmt_tokens[x_management_token]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")


async def authenticate_pin(req: ManagementPinRequest) -> ManagementAuthResponse:
    """Validate the management PIN and issue a session token."""
    _verify_pin(req.pin)
    return ManagementAuthResponse(token=create_management_token())


async def get_chip_info(uid: str, chip_client: FingerprintsClient) -> ChipInfoResponse:
    """Look up chip details by UID for the management panel."""
    try:
        chip = await chip_client.validate(uid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found") from None
    return ChipInfoResponse(
        uid=chip.uid,
        chip_id=chip.chip_id,
        balance_cents=chip.balance_cents,
        is_enabled=chip.is_enabled,
        holder_name=chip.holder_name,
    )


async def topup_chip(req: ChipTopupRequest, chip_client: FingerprintsClient) -> ChipTopupResponse:
    """Register the chip if needed and add the requested balance."""
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
    """Request a manual door unlock via the hardware service."""
    await hardware_client.open_door(seconds=settings.door_unlock_seconds)


async def list_users(chip_client: FingerprintsClient) -> list[ManagementUserResponse]:
    """Return all registered ledger users for the management panel."""
    users = await chip_client.list_users()
    return [
        ManagementUserResponse(
            chip_id=u.chip_id,
            uid=u.uid,
            holder_name=u.holder_name,
            is_enabled=u.is_enabled,
            balance_cents=u.balance_cents,
            created_at=u.created_at,
        )
        for u in users
    ]


async def update_user(
    chip_id: str,
    req: ManagementUserUpdateRequest,
    chip_client: FingerprintsClient,
) -> ManagementUserResponse:
    """Update a registered user's name and/or enabled flag."""
    try:
        user = await chip_client.update_user(
            chip_id,
            holder_name=req.holder_name,
            is_enabled=req.is_enabled,
            set_holder_name="holder_name" in req.model_fields_set,
            set_is_enabled="is_enabled" in req.model_fields_set,
        )
    except ValueError as exc:
        if str(exc) == "chip_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found") from None
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return ManagementUserResponse(
        chip_id=user.chip_id,
        uid=user.uid,
        holder_name=user.holder_name,
        is_enabled=user.is_enabled,
        balance_cents=user.balance_cents,
        created_at=user.created_at,
    )


async def delete_user(
    chip_id: str,
    chip_client: FingerprintsClient,
    hardware_client: HardwareClient,
) -> None:
    """Delete a ledger user and clear the fingerprint sensor slot when applicable."""
    meta = await chip_client.get_by_id(chip_id)
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found")

    slot = uid_to_slot(meta.uid)
    if slot is not None:
        try:
            await hardware_client.delete_fingerprint(slot)
        except HardwareUnavailableError:
            logger.warning("fingerprint_delete_hardware_unavailable chip_id=%s slot=%s", chip_id, slot)
        except Exception:
            logger.exception("fingerprint_delete_hardware_failed chip_id=%s slot=%s", chip_id, slot)

    try:
        await chip_client.delete_user(chip_id)
    except ValueError as exc:
        if str(exc) == "chip_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found") from None
        raise
