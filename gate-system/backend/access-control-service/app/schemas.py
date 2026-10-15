import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AccessDecisionResponse(BaseModel):
    granted: bool
    reason: str
    member_id: uuid.UUID | None = None
    fee_cents: int
    balance_before_cents: int | None = None
    balance_after_cents: int | None = None


class SimulateCashRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=1_000_00)


class SimulateCashResponse(BaseModel):
    granted: bool
    accumulated_cents: int
    entrance_fee_cents: int
    remaining_cents: int = 0


class FingerprintApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=8, max_length=64)


class FingerprintEnrollRequest(BaseModel):
    holder_name: str = Field(min_length=2, max_length=80)
    national_id: str = Field(min_length=1, max_length=9)
    initial_amount_cents: int = Field(default=0, ge=0, le=1_000_000)


class FingerprintEnrollCancelRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)


class FingerprintEnrollStartResponse(BaseModel):
    session_id: str
    holder_name: str
    national_id: str
    initial_amount_cents: int


class AccessLogResponse(BaseModel):
    id: int
    member_id: uuid.UUID | None
    uid: str | None
    decision: str
    reason: str
    fee_cents: int
    balance_before_cents: int | None
    balance_after_cents: int | None
    created_at: datetime

