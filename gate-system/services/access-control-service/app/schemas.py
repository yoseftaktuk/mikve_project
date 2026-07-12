import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AccessAttemptRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)


class AccessDecisionResponse(BaseModel):
    granted: bool
    reason: str
    chip_id: uuid.UUID | None = None
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


class AccessLogResponse(BaseModel):
    id: int
    chip_id: uuid.UUID | None
    uid: str | None
    decision: str
    reason: str
    fee_cents: int
    balance_before_cents: int | None
    balance_after_cents: int | None
    created_at: datetime

