from pydantic import Field

from gate_shared.settings import CommonSettings


class Settings(CommonSettings):
    service_name: str = "payment-service"
    jwt_secret: str = "not_used"
    postgres_schema: str = "payment_service"

    # mock: fake card clearing for local dev; nedarim: real Nedarim Plus integration.
    payment_mode: str = Field(default="mock", alias="PAYMENT_MODE")

    # Nedarim Plus institution credentials. ApiValid is all the server-side
    # transaction flow needs; ApiPassword (which can pull data out of the
    # institution) is intentionally never stored here.
    nedarim_mosad: str = Field(default="", alias="NEDARIM_MOSAD")
    nedarim_api_valid: str = Field(default="", alias="NEDARIM_API_VALID")
    nedarim_groupe: str = Field(default="", alias="NEDARIM_GROUPE")
    nedarim_api_url: str = Field(
        default="https://matara.pro/nedarimplus/V6/Files/WebServices/DebitIframe.aspx",
        alias="NEDARIM_API_URL",
    )
    nedarim_iframe_url: str = Field(
        default="https://www.matara.pro/nedarimplus/iframe/", alias="NEDARIM_IFRAME_URL"
    )
    nedarim_timeout_seconds: float = Field(default=15.0, alias="NEDARIM_TIMEOUT_SECONDS")

    # Origin Nedarim reaches us on. Their servers are outside the LAN, so this is
    # the tunnel/public hostname rather than the internal nginx address.
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")

    # Reject callbacks that did not arrive through Cloudflare (no CF-Connecting-IP).
    # Local unit tests call process_nedarim_callback directly and skip this gate.
    nedarim_require_cloudflare: bool = Field(default=True, alias="NEDARIM_REQUIRE_CLOUDFLARE")

    topup_amounts_cents: str = Field(default="2000,5000,10000", alias="TOPUP_AMOUNTS_CENTS")

    @property
    def topup_amount_options_cents(self) -> tuple[int, ...]:
        """Amounts the kiosk may ask for. Anything else never reaches Nedarim."""
        return tuple(int(part) for part in self.topup_amounts_cents.split(",") if part.strip())


settings = Settings()
