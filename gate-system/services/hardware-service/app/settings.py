from pydantic import Field

from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "hardware-service"

    # Hardware service does not authenticate end-users in the starter.
    jwt_secret: str = "not_used"

    hardware_mode: str = Field(default="mock", alias="HARDWARE_MODE")  # mock|rpi

    door_relay_gpio_pin: int = Field(default=22, alias="DOOR_RELAY_GPIO_PIN")
    door_unlock_seconds: int = Field(default=5, alias="DOOR_UNLOCK_SECONDS")

    rfid_serial_port: str = Field(default="/dev/ttyUSB0", alias="RFID_SERIAL_PORT")
    rfid_baudrate: int = Field(default=9600, alias="RFID_BAUDRATE")

    coin_acceptor_gpio_pin: int = Field(default=17, alias="COIN_ACCEPTOR_GPIO_PIN")


settings = Settings()

