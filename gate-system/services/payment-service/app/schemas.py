from pydantic import BaseModel, Field


class ChargeChipRequest(BaseModel):
    amount: float = Field(ge=1, description="Charge amount in ILS (shekels)")


class ChargeChipResponse(BaseModel):
    message: str = "Chip charged successfully."
