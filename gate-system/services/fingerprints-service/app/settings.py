from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "fingerprints-service"
    postgres_schema: str = "fingerprints_service"


settings = Settings()

