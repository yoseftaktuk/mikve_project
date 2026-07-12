from pydantic import BaseModel, Field


class DoorOpenRequest(BaseModel):
    seconds: int = Field(default=5, ge=1, le=10)


class SimulateRfidRequest(BaseModel):
    uid: str = Field(min_length=4, max_length=64)


class SimulateCashRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=1_000_00)

