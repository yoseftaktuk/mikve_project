from __future__ import annotations

import logging
import secrets
import time

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel, Field, model_validator

from .clients import FingerprintsClient, HardwareClient, HardwareUnavailableError
from .fingerprint_logic import uid_to_slot
from .settings import management_cookie_secure_enabled, settings

logger = logging.getLogger(__name__)

_mgmt_tokens: dict[str, float] = {}
TOKEN_TTL_SECONDS = 60 * 60 * 8  # 8 hours
MANAGEMENT_COOKIE_NAME = "gate_management_token"
MANAGEMENT_COOKIE_PATH = "/"
MANAGEMENT_COOKIE_SAMESITE = "lax"


class ManagementPinRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


class ManagementAuthResponse(BaseModel):
    authenticated: bool = True


class ManagementSessionResponse(BaseModel):
    authenticated: bool


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
    subscription_active: bool = False
    subscription_month_name: str | None = None
    subscription_free_entry_available_today: bool = False
    current_hebrew_month_name: str | None = None


class ManagementUserResponse(BaseModel):
    chip_id: str
    uid: str
    holder_name: str | None = None
    national_id: str | None = None
    is_enabled: bool
    balance_cents: int
    created_at: str | None = None


class ManagementUserUpdateRequest(BaseModel):
    holder_name: str | None = Field(default=None, max_length=80)
    national_id: str | None = Field(default=None, max_length=9)
    is_enabled: bool | None = None
    balance_cents: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> ManagementUserUpdateRequest:
        if (
            "holder_name" not in self.model_fields_set
            and "national_id" not in self.model_fields_set
            and "is_enabled" not in self.model_fields_set
            and "balance_cents" not in self.model_fields_set
        ):
            raise ValueError("Provide holder_name, national_id, is_enabled, and/or balance_cents")
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


def _is_token_valid(token: str | None) -> bool:
    """Return True when the opaque management token is present and unexpired."""
    if not token:
        return False
    _purge_expired_tokens()
    expires_at = _mgmt_tokens.get(token)
    if expires_at is None:
        return False
    if expires_at <= time.time():
        del _mgmt_tokens[token]
        return False
    return True


def set_management_cookie(response: Response, token: str) -> None:
    """Attach the HttpOnly management session cookie to the response."""
    response.set_cookie(
        key=MANAGEMENT_COOKIE_NAME,
        value=token,
        max_age=TOKEN_TTL_SECONDS,
        path=MANAGEMENT_COOKIE_PATH,
        httponly=True,
        secure=management_cookie_secure_enabled(),
        samesite=MANAGEMENT_COOKIE_SAMESITE,
    )


def clear_management_cookie(response: Response) -> None:
    """Expire the management session cookie with matching security attributes."""
    response.set_cookie(
        key=MANAGEMENT_COOKIE_NAME,
        value="",
        max_age=0,
        path=MANAGEMENT_COOKIE_PATH,
        httponly=True,
        secure=management_cookie_secure_enabled(),
        samesite=MANAGEMENT_COOKIE_SAMESITE,
    )


def require_management_token(request: Request) -> None:
    """FastAPI dependency that requires a valid management session cookie."""
    if not settings.management_pin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="management_disabled")
    token = request.cookies.get(MANAGEMENT_COOKIE_NAME)
    if not token or token not in _mgmt_tokens:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token")
    if _mgmt_tokens[token] <= time.time():
        del _mgmt_tokens[token]
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token_expired")


def get_management_session(request: Request) -> ManagementSessionResponse:
    """Return whether the request carries a valid management session cookie."""
    if not settings.management_pin:
        return ManagementSessionResponse(authenticated=False)
    token = request.cookies.get(MANAGEMENT_COOKIE_NAME)
    return ManagementSessionResponse(authenticated=_is_token_valid(token))


async def authenticate_pin(req: ManagementPinRequest, response: Response) -> ManagementAuthResponse:
    """Validate the management PIN and set an HttpOnly session cookie."""
    _verify_pin(req.pin)
    token = create_management_token()
    set_management_cookie(response, token)
    return ManagementAuthResponse(authenticated=True)


async def logout_management(request: Request, response: Response) -> ManagementSessionResponse:
    """Revoke the current management session token and clear the cookie."""
    token = request.cookies.get(MANAGEMENT_COOKIE_NAME)
    if token and token in _mgmt_tokens:
        del _mgmt_tokens[token]
    clear_management_cookie(response)
    return ManagementSessionResponse(authenticated=False)


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
        subscription_active=chip.subscription_active,
        subscription_month_name=chip.subscription_month_name,
        subscription_free_entry_available_today=chip.subscription_free_entry_available_today,
        current_hebrew_month_name=chip.current_hebrew_month_name,
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
            national_id=u.national_id,
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
    """Update a registered user's name, national ID, enabled flag, and/or absolute balance."""
    from gate_shared.national_id import InvalidNationalIdError, normalize_national_id

    set_name = "holder_name" in req.model_fields_set
    set_national_id = "national_id" in req.model_fields_set
    set_enabled = "is_enabled" in req.model_fields_set
    set_balance = "balance_cents" in req.model_fields_set

    national_id_value = req.national_id
    if set_national_id and req.national_id is not None and req.national_id.strip():
        try:
            national_id_value = normalize_national_id(req.national_id)
        except InvalidNationalIdError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_national_id"
            ) from None
    elif set_national_id:
        national_id_value = None

    if set_name or set_national_id or set_enabled:
        try:
            user = await chip_client.update_user(
                chip_id,
                holder_name=req.holder_name,
                national_id=national_id_value,
                is_enabled=req.is_enabled,
                set_holder_name=set_name,
                set_national_id=set_national_id,
                set_is_enabled=set_enabled,
            )
        except ValueError as exc:
            if str(exc) == "chip_not_found":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found") from None
            if str(exc) == "national_id_taken":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="national_id_taken") from None
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    else:
        user = await chip_client.get_by_id(chip_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chip_not_found")

    balance_cents = user.balance_cents
    if set_balance and req.balance_cents is not None:
        target = req.balance_cents
        delta = target - balance_cents
        if delta != 0:
            try:
                balance_cents = await chip_client.adjust_balance(
                    chip_id=chip_id,
                    delta_cents=delta,
                    reason="management_set_balance",
                    description=f"management set balance to {target}",
                )
            except ValueError as exc:
                if str(exc) == "insufficient_balance":
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT, detail="insufficient_balance"
                    ) from None
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        else:
            balance_cents = target

    return ManagementUserResponse(
        chip_id=user.chip_id,
        uid=user.uid,
        holder_name=user.holder_name,
        national_id=user.national_id,
        is_enabled=user.is_enabled,
        balance_cents=balance_cents,
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
