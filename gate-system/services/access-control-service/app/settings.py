from pydantic import Field

from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "access-control-service"
    postgres_schema: str = "access_service"

    entrance_fee_cents: int = Field(default=500, alias="ENTRANCE_FEE_CENTS")
    door_unlock_seconds: int = Field(default=5, alias="DOOR_UNLOCK_SECONDS")
    cash_session_timeout_seconds: int = Field(default=20, alias="CASH_SESSION_TIMEOUT_SECONDS")
    management_pin: str = Field(default="", alias="MANAGEMENT_PIN")
    # When unset, Secure cookies are enabled outside local/dev/test environments.
    management_cookie_secure: bool | None = Field(default=None, alias="MANAGEMENT_COOKIE_SECURE")
    # How long a scanned fingerprint waits for staff confirmation before it is dropped.
    fingerprint_approval_timeout_seconds: int = Field(
        default=25, alias="FINGERPRINT_APPROVAL_TIMEOUT_SECONDS"
    )

    # Access-attempt saga (door confirm + compensation).
    door_open_timeout_ms: int = Field(default=3000, alias="DOOR_OPEN_TIMEOUT_MS")
    door_max_retries: int = Field(default=2, alias="DOOR_MAX_RETRIES")
    door_retry_delay_ms: int = Field(default=500, alias="DOOR_RETRY_DELAY_MS")
    refund_timeout_ms: int = Field(default=5000, alias="REFUND_TIMEOUT_MS")
    refund_max_retries: int = Field(default=3, alias="REFUND_MAX_RETRIES")
    max_saga_duration_ms: int = Field(default=30000, alias="MAX_SAGA_DURATION_MS")
    stale_attempt_seconds: int = Field(default=60, alias="STALE_ATTEMPT_SECONDS")
    repeated_failure_threshold: int = Field(default=5, alias="REPEATED_FAILURE_THRESHOLD")
    cash_receipt_code_bytes: int = Field(default=8, alias="CASH_RECEIPT_CODE_BYTES")
    access_saga_enabled: bool = Field(default=True, alias="ACCESS_SAGA_ENABLED")


settings = Settings()


def management_cookie_secure_enabled() -> bool:
    """Return whether the management session cookie should use the Secure flag."""
    if settings.management_cookie_secure is not None:
        return settings.management_cookie_secure
    return settings.environment.lower() not in ("dev", "local", "test")
