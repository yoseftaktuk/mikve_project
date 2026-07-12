from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "payment-service"
    jwt_secret: str = "not_used"


settings = Settings()
