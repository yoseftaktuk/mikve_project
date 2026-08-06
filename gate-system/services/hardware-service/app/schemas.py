from pydantic import BaseModel, Field


class DoorOpenRequest(BaseModel):
    seconds: int = Field(default=5, ge=1, le=10)
    operation_id: str | None = Field(default=None, max_length=64)
    attempt_id: str | None = Field(default=None, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=64)


class DoorOpenResponse(BaseModel):
    operation_id: str | None = None
    status: str
    unlocked_for_seconds: int


class SimulateCashRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=1_000_00)


class SimulateFingerprintRequest(BaseModel):
    # None simulates a finger that matches no stored template.
    slot: int | None = Field(default=None, ge=0, le=1000)


class FingerprintEnrollRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)


class FingerprintDeleteRequest(BaseModel):
    slot: int = Field(ge=0, le=1000)

