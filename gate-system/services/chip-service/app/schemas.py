import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChipCreateRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)


class ChipAssignRequest(BaseModel):
    user_id: uuid.UUID | None = None


class ChipResponse(BaseModel):
    id: uuid.UUID
    uid: str
    is_enabled: bool
    assigned_user_id: uuid.UUID | None
    created_at: datetime


class BalanceResponse(BaseModel):
    chip_id: uuid.UUID
    amount_cents: int
    updated_at: datetime


class AdjustBalanceRequest(BaseModel):
    delta_cents: int = Field(ge=-1_000_000_000, le=1_000_000_000)
    description: str | None = Field(default=None, max_length=255)
    reason: str = Field(default="adjustment", max_length=40)


class ValidateChipRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)


class ValidateChipResponse(BaseModel):
    chip_id: uuid.UUID
    uid: str
    is_enabled: bool
    assigned_user_id: uuid.UUID | None
    balance_cents: int


class ChipActivityResponse(BaseModel):
    id: int
    chip_id: uuid.UUID
    event_type: str
    delta_cents: int
    description: str | None
    created_at: datetime

