from pydantic import Field

from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "access-control-service"
    postgres_schema: str = "access_service"

    entrance_fee_cents: int = Field(default=500, alias="ENTRANCE_FEE_CENTS")
    door_unlock_seconds: int = Field(default=5, alias="DOOR_UNLOCK_SECONDS")
    cash_session_timeout_seconds: int = Field(default=20, alias="CASH_SESSION_TIMEOUT_SECONDS")
    management_pin: str = Field(default="", alias="MANAGEMENT_PIN")
    # How long a scanned fingerprint waits for staff confirmation before it is dropped.
    fingerprint_approval_timeout_seconds: int = Field(
        default=25, alias="FINGERPRINT_APPROVAL_TIMEOUT_SECONDS"
    )


settings = Settings()

