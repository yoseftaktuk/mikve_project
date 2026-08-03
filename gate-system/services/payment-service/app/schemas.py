import uuid

from pydantic import BaseModel, Field


class ChargeChipRequest(BaseModel):
    amount: float = Field(ge=1, description="Charge amount in ILS (shekels)")


class ChargeChipResponse(BaseModel):
    message: str = "Chip charged successfully."


class CardTopupCreateRequest(BaseModel):
    chip_uid: str = Field(min_length=4, max_length=64)
    amount_cents: int = Field(gt=0, le=1_000_000)


class CardTopupCreateResponse(BaseModel):
    topup_id: uuid.UUID
    nedarim_transaction_id: str
    iframe_url: str
    amount_cents: int
    chip_uid: str
    chip_id: uuid.UUID


class CardTopupStatusResponse(BaseModel):
    topup_id: uuid.UUID
    status: str
    amount_cents: int
    chip_uid: str
    chip_id: uuid.UUID
    nedarim_transaction_id: str | None = None
    balance_after_cents: int | None = None
    last_num: str | None = None
    error_code: str | None = None
