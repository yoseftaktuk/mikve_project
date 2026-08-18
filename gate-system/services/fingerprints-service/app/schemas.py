import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from gate_shared.national_id import InvalidNationalIdError, normalize_national_id


def _optional_national_id(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return normalize_national_id(stripped)
    except InvalidNationalIdError as exc:
        raise ValueError("invalid_national_id") from exc


class MemberCreateRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)
    holder_name: str | None = Field(default=None, max_length=80)
    national_id: str | None = Field(default=None, max_length=9)

    @field_validator("national_id")
    @classmethod
    def validate_national_id(cls, value: str | None) -> str | None:
        return _optional_national_id(value)


class MemberAssignRequest(BaseModel):
    user_id: uuid.UUID | None = None


class MemberRenameRequest(BaseModel):
    holder_name: str | None = Field(default=None, max_length=80)
    national_id: str | None = Field(default=None, max_length=9)

    @field_validator("national_id")
    @classmethod
    def validate_national_id(cls, value: str | None) -> str | None:
        return _optional_national_id(value)


class MemberUpdateRequest(BaseModel):
    holder_name: str | None = Field(default=None, max_length=80)
    national_id: str | None = Field(default=None, max_length=9)
    is_enabled: bool | None = None

    @field_validator("national_id")
    @classmethod
    def validate_national_id(cls, value: str | None) -> str | None:
        return _optional_national_id(value)


class MemberResponse(BaseModel):
    id: uuid.UUID
    uid: str
    holder_name: str | None
    national_id: str | None = None
    is_enabled: bool
    assigned_user_id: uuid.UUID | None
    created_at: datetime


class MemberListItemResponse(BaseModel):
    id: uuid.UUID
    uid: str
    holder_name: str | None
    national_id: str | None = None
    is_enabled: bool
    balance_cents: int
    created_at: datetime


class BalanceResponse(BaseModel):
    member_id: uuid.UUID
    amount_cents: int
    updated_at: datetime


class AdjustBalanceRequest(BaseModel):
    delta_cents: int = Field(ge=-1_000_000_000, le=1_000_000_000)
    description: str | None = Field(default=None, max_length=255)
    reason: str = Field(default="adjustment", max_length=40)
    # When set, a second call with the same key returns the current balance
    # without applying the delta again.
    idempotency_key: str | None = Field(default=None, max_length=80)


class LookupByNationalIdRequest(BaseModel):
    """Exact national-id lookup. No checksum — Nedarim Zeout may not be a valid ID."""

    national_id: str = Field(min_length=1, max_length=9)


class LookupByNationalIdResponse(BaseModel):
    member_id: uuid.UUID
    uid: str
    is_enabled: bool
    balance_cents: int
    national_id: str | None = None


class ValidateMemberRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)


class ValidateMemberResponse(BaseModel):
    member_id: uuid.UUID
    uid: str
    holder_name: str | None = None
    national_id: str | None = None
    is_enabled: bool
    assigned_user_id: uuid.UUID | None
    balance_cents: int
    subscription_active: bool = False
    subscription_month_name: str | None = None
    subscription_free_entry_available_today: bool = False
    current_hebrew_month_name: str | None = None


class ActivateSubscriptionRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=1_000_000)
    nedarim_transaction_id: str = Field(min_length=1, max_length=64)
    hebrew_year: int | None = Field(default=None, ge=1)
    hebrew_month: int | None = Field(default=None, ge=1, le=13)
    hebrew_month_name: str | None = Field(default=None, max_length=32)


class MarkFreeEntryRequest(BaseModel):
    """Optional body so a retried saga grant does not consume a second slot."""

    idempotency_key: str | None = Field(default=None, max_length=64)


class SubscriptionResponse(BaseModel):
    member_id: uuid.UUID
    hebrew_year: int
    hebrew_month: int
    hebrew_month_name: str
    amount_cents: int
    nedarim_transaction_id: str
    status: str
    last_free_entry_on: date | None = None
    purchased_at: datetime
    subscription_active: bool
    subscription_free_entry_available_today: bool
    current_hebrew_month_name: str


class MemberActivityResponse(BaseModel):
    id: int
    member_id: uuid.UUID
    event_type: str
    delta_cents: int
    description: str | None
    idempotency_key: str | None = None
    created_at: datetime
