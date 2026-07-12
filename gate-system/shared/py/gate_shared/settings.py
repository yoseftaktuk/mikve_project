from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "service"
    environment: str = "dev"
    log_level: str = "INFO"

    # Auth
    jwt_issuer: str = "gate-system"
    jwt_audience: str = "gate-system"
    jwt_secret: str
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 60 * 60 * 24 * 30

    # Postgres (optional for services without DB)
    postgres_dsn: str | None = None

    # Redis
    redis_url: str

    # Internal service URLs (for service-to-service calls)
    chip_service_url: str = "http://chip-service:8000"
    hardware_service_url: str = "http://hardware-service:8000"
    access_service_url: str = "http://access-control-service:8000"

