from .clients import ChipClient
from .settings import settings

DEMO_CHIP_UID = "DEMO-UID-1234"


async def ensure_demo_chip(chip_client: ChipClient) -> None:
    fee = settings.entrance_fee_cents
    target_balance = fee + 1000

    try:
        chip = await chip_client.validate(DEMO_CHIP_UID)
    except ValueError:
        await chip_client.register(DEMO_CHIP_UID)
        chip = await chip_client.validate(DEMO_CHIP_UID)

    if chip.balance_cents < fee:
        await chip_client.adjust_balance(
            chip_id=chip.chip_id,
            delta_cents=target_balance - chip.balance_cents,
            reason="dev_topup",
            description="simulation top-up",
        )
