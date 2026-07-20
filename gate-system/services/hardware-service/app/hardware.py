import asyncio
import logging
from dataclasses import dataclass

from .rpi_gpio import RpiGpioController

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareStatus:
    """Snapshot of hardware connection status for RFID, coins, and door."""

    mode: str
    rfid_reader_connected: bool
    coin_acceptor_connected: bool
    door_relay_connected: bool


class HardwareAdapter:
    """Interface for mock or Raspberry Pi hardware control."""

    async def get_status(self) -> HardwareStatus:
        """Return current hardware connection status."""
        raise NotImplementedError

    async def open_door(self, *, seconds: int) -> None:
        """Unlock the door relay for the given duration."""
        raise NotImplementedError

    async def simulate_rfid_scan(self, uid: str) -> None:
        """Simulate an RFID chip scan (mock mode)."""
        raise NotImplementedError

    async def simulate_cash_inserted(self, amount_cents: int) -> None:
        """Simulate cash insertion (mock mode)."""
        raise NotImplementedError

    async def start(self) -> None:
        """Initialize hardware listeners."""
        return None

    async def stop(self) -> None:
        """Tear down hardware listeners."""
        return None


class MockHardwareAdapter(HardwareAdapter):
    """In-memory adapter used for local development without GPIO."""

    def __init__(self, on_rfid_scan, on_cash_inserted):
        self._on_rfid_scan = on_rfid_scan
        self._on_cash_inserted = on_cash_inserted

    async def get_status(self) -> HardwareStatus:
        """Return a connected status for all mock devices."""
        return HardwareStatus(
            mode="mock",
            rfid_reader_connected=True,
            coin_acceptor_connected=True,
            door_relay_connected=True,
        )

    async def open_door(self, *, seconds: int) -> None:
        """Log a simulated door open/close cycle."""
        logger.info("door_open seconds=%s", seconds)
        await asyncio.sleep(min(seconds, 10))
        logger.info("door_closed")

    async def simulate_rfid_scan(self, uid: str) -> None:
        """Forward a fake RFID scan to the configured callback."""
        await self._on_rfid_scan(uid)

    async def simulate_cash_inserted(self, amount_cents: int) -> None:
        """Forward fake cash insertion to the configured callback."""
        await self._on_cash_inserted(amount_cents)


class RpiHardwareAdapter(HardwareAdapter):
    """Adapter that drives real Raspberry Pi GPIO and USB RFID."""

    def __init__(
        self,
        *,
        on_rfid_scan,
        on_cash_inserted,
        coin_pin: int,
        door_pin: int,
        door_unlock_seconds: int,
        rfid_serial_port: str,
        rfid_baudrate: int,
    ) -> None:
        self._on_rfid_scan = on_rfid_scan
        self._on_cash_inserted = on_cash_inserted
        self._door_unlock_seconds = door_unlock_seconds
        self._gpio: RpiGpioController | None = None
        self._coin_pin = coin_pin
        self._door_pin = door_pin
        self._rfid_serial_port = rfid_serial_port
        self._rfid_baudrate = rfid_baudrate

    async def start(self) -> None:
        """Start GPIO coin listening and optional RFID serial reading."""
        loop = asyncio.get_running_loop()

        async def on_cash_shekels(shekels: float) -> None:
            amount_cents = int(round(shekels * 100))
            await self._on_cash_inserted(amount_cents)

        self._gpio = RpiGpioController(
            coin_pin=self._coin_pin,
            door_pin=self._door_pin,
            on_cash_shekels=on_cash_shekels,
            on_rfid_uid=self._on_rfid_scan,
            rfid_serial_port=self._rfid_serial_port,
            rfid_baudrate=self._rfid_baudrate,
            loop=loop,
        )
        await asyncio.to_thread(self._gpio.start)
        logger.info("rpi_adapter_started")

    async def stop(self) -> None:
        """Stop GPIO and RFID background threads."""
        if self._gpio is not None:
            await asyncio.to_thread(self._gpio.stop)
            self._gpio = None

    async def get_status(self) -> HardwareStatus:
        """Report whether the GPIO controller and RFID reader are ready."""
        gpio_ready = self._gpio is not None and self._gpio.gpio_ready
        rfid_ready = self._gpio is not None and self._gpio.rfid_connected
        return HardwareStatus(
            mode="rpi",
            rfid_reader_connected=rfid_ready,
            coin_acceptor_connected=gpio_ready,
            door_relay_connected=gpio_ready,
        )

    async def open_door(self, *, seconds: int) -> None:
        """Pulse the door relay HIGH for the given seconds."""
        if self._gpio is None:
            raise RuntimeError("GPIO controller is not running")
        await asyncio.to_thread(self._gpio.open_door_sync, seconds)

    async def simulate_rfid_scan(self, uid: str) -> None:
        """Reject RFID simulation when running on real hardware."""
        raise NotImplementedError("dev endpoints are disabled in rpi mode")

    async def simulate_cash_inserted(self, amount_cents: int) -> None:
        """Reject cash simulation when running on real hardware."""
        raise NotImplementedError("dev endpoints are disabled in rpi mode")
