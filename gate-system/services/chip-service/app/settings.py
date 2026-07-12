from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "chip-service"
    postgres_schema: str = "chip_service"


settings = Settings()

