import asyncio
import itertools
import logging
from dataclasses import dataclass

from .fingerprint import (
    STEP_PLACE_AGAIN,
    STEP_PLACE_FINGER,
    STEP_REMOVE_FINGER,
    STEP_STORED,
    FingerprintController,
    fingerprint_enabled,
)
from .rpi_gpio import RpiGpioController

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HardwareStatus:
    """Snapshot of hardware connection status for RFID, coins, door, and fingerprint."""

    mode: str
    rfid_reader_connected: bool
    coin_acceptor_connected: bool
    door_relay_connected: bool
    fingerprint_reader_connected: bool


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

    async def simulate_fingerprint_scan(self, slot: int | None) -> None:
        """Simulate a fingerprint match, or a no-match when slot is None (mock mode)."""
        raise NotImplementedError

    async def enroll_fingerprint(self, session_id: str) -> dict[str, object]:
        """Run a fingerprint enrollment and return its terminal step and slot."""
        raise NotImplementedError

    async def cancel_enroll(self) -> None:
        """Abort a running fingerprint enrollment."""
        raise NotImplementedError

    async def start(self) -> None:
        """Initialize hardware listeners."""
        return None

    async def stop(self) -> None:
        """Tear down hardware listeners."""
        return None


class MockHardwareAdapter(HardwareAdapter):
    """In-memory adapter used for local development without GPIO."""

    def __init__(
        self,
        on_rfid_scan,
        on_cash_inserted,
        on_fingerprint_scan=None,
        on_fingerprint_unmatched=None,
        on_fingerprint_progress=None,
    ):
        self._on_rfid_scan = on_rfid_scan
        self._on_cash_inserted = on_cash_inserted
        self._on_fingerprint_scan = on_fingerprint_scan
        self._on_fingerprint_unmatched = on_fingerprint_unmatched
        self._on_fingerprint_progress = on_fingerprint_progress
        self._next_slot = itertools.count(1)
        self._enroll_cancelled = False

    async def get_status(self) -> HardwareStatus:
        """Return a connected status for all mock devices."""
        return HardwareStatus(
            mode="mock",
            rfid_reader_connected=True,
            coin_acceptor_connected=True,
            door_relay_connected=True,
            fingerprint_reader_connected=True,
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

    async def simulate_fingerprint_scan(self, slot: int | None) -> None:
        """Forward a fake fingerprint match (or no-match) to the callbacks."""
        if slot is None:
            if self._on_fingerprint_unmatched is not None:
                await self._on_fingerprint_unmatched()
            return
        if self._on_fingerprint_scan is not None:
            await self._on_fingerprint_scan(slot, 100)

    async def enroll_fingerprint(self, session_id: str) -> dict[str, object]:
        """Walk through the enrollment steps with short pauses so the UI can follow."""
        self._enroll_cancelled = False
        for step in (STEP_PLACE_FINGER, STEP_REMOVE_FINGER, STEP_PLACE_AGAIN):
            if self._enroll_cancelled:
                return {"step": "cancelled", "slot": None}
            await self._emit_progress(session_id, step, None)
            await asyncio.sleep(1.0)

        if self._enroll_cancelled:
            return {"step": "cancelled", "slot": None}

        slot = next(self._next_slot)
        await self._emit_progress(session_id, STEP_STORED, slot)
        return {"step": STEP_STORED, "slot": slot}

    async def cancel_enroll(self) -> None:
        """Mark the simulated enrollment as cancelled."""
        self._enroll_cancelled = True

    async def _emit_progress(self, session_id: str, step: str, slot: int | None) -> None:
        if self._on_fingerprint_progress is not None:
            await self._on_fingerprint_progress(session_id, step, slot)


class RpiHardwareAdapter(HardwareAdapter):
    """Adapter that drives real Raspberry Pi GPIO, USB RFID, and UART fingerprint."""

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
        door_relay_idle_high: bool = False,
        on_fingerprint_scan=None,
        on_fingerprint_unmatched=None,
        on_fingerprint_progress=None,
        fingerprint_serial_port: str = "",
        fingerprint_baudrate: int = 57600,
    ) -> None:
        self._on_rfid_scan = on_rfid_scan
        self._on_cash_inserted = on_cash_inserted
        self._door_unlock_seconds = door_unlock_seconds
        self._gpio: RpiGpioController | None = None
        self._coin_pin = coin_pin
        self._door_pin = door_pin
        self._rfid_serial_port = rfid_serial_port
        self._rfid_baudrate = rfid_baudrate
        self._door_relay_idle_high = door_relay_idle_high
        self._on_fingerprint_scan = on_fingerprint_scan
        self._on_fingerprint_unmatched = on_fingerprint_unmatched
        self._on_fingerprint_progress = on_fingerprint_progress
        self._fingerprint_serial_port = fingerprint_serial_port
        self._fingerprint_baudrate = fingerprint_baudrate
        self._fingerprint: FingerprintController | None = None

    async def start(self) -> None:
        """Start GPIO coin listening plus optional RFID and fingerprint readers."""
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
            door_relay_idle_high=self._door_relay_idle_high,
            loop=loop,
        )
        await asyncio.to_thread(self._gpio.start)

        fingerprint_callbacks_ready = None not in (
            self._on_fingerprint_scan,
            self._on_fingerprint_unmatched,
            self._on_fingerprint_progress,
        )
        if fingerprint_enabled(self._fingerprint_serial_port) and fingerprint_callbacks_ready:
            self._fingerprint = FingerprintController(
                serial_port=self._fingerprint_serial_port,
                baudrate=self._fingerprint_baudrate,
                on_identified=self._on_fingerprint_scan,
                on_unmatched=self._on_fingerprint_unmatched,
                on_progress=self._on_fingerprint_progress,
                loop=loop,
            )
            await asyncio.to_thread(self._fingerprint.start)
        else:
            logger.info("fingerprint_reader_disabled no FINGERPRINT_SERIAL_PORT configured")

        logger.info("rpi_adapter_started")

    async def stop(self) -> None:
        """Stop GPIO, RFID, and fingerprint background threads."""
        if self._fingerprint is not None:
            await asyncio.to_thread(self._fingerprint.stop)
            self._fingerprint = None
        if self._gpio is not None:
            await asyncio.to_thread(self._gpio.stop)
            self._gpio = None

    async def get_status(self) -> HardwareStatus:
        """Report whether the GPIO controller and serial readers are ready."""
        gpio_ready = self._gpio is not None and self._gpio.gpio_ready
        rfid_ready = self._gpio is not None and self._gpio.rfid_connected
        fingerprint_ready = self._fingerprint is not None and self._fingerprint.connected
        return HardwareStatus(
            mode="rpi",
            rfid_reader_connected=rfid_ready,
            coin_acceptor_connected=gpio_ready,
            door_relay_connected=gpio_ready,
            fingerprint_reader_connected=fingerprint_ready,
        )

    def get_coin_debug(self) -> dict:
        """Return live coin-pulse counters when GPIO is running."""
        if self._gpio is None:
            return {"gpio": None}
        return self._gpio.debug_coin_stats()

    async def sample_coin_pin(self, duration_s: float = 5.0) -> dict:
        """Sample the coin GPIO pin for level transitions (debug)."""
        if self._gpio is None:
            return {"error": "gpio_not_ready"}
        return await asyncio.to_thread(self._gpio.sample_coin_pin, duration_s)

    async def selftest_coin_pin(self) -> dict:
        """Run pull-up/down self-test on the coin pin (debug)."""
        if self._gpio is None:
            return {"error": "gpio_not_ready"}
        return await asyncio.to_thread(self._gpio.selftest_coin_pin)

    async def open_door(self, *, seconds: int) -> None:
        """Float the door pin (like unplugging IN1) for the given seconds."""
        if self._gpio is None:
            raise RuntimeError("GPIO controller is not running")
        await asyncio.to_thread(self._gpio.open_door_sync, seconds)

    async def simulate_rfid_scan(self, uid: str) -> None:
        """Reject RFID simulation when running on real hardware."""
        raise NotImplementedError("dev endpoints are disabled in rpi mode")

    async def simulate_cash_inserted(self, amount_cents: int) -> None:
        """Reject cash simulation when running on real hardware."""
        raise NotImplementedError("dev endpoints are disabled in rpi mode")

    async def simulate_fingerprint_scan(self, slot: int | None) -> None:
        """Reject fingerprint simulation when running on real hardware."""
        raise NotImplementedError("dev endpoints are disabled in rpi mode")

    async def enroll_fingerprint(self, session_id: str) -> dict[str, object]:
        """Run the blocking sensor enrollment on a worker thread."""
        if self._fingerprint is None:
            raise RuntimeError("fingerprint reader is not configured")
        return await asyncio.to_thread(self._fingerprint.enroll_sync, session_id)

    async def cancel_enroll(self) -> None:
        """Ask the sensor enrollment to abort."""
        if self._fingerprint is None:
            return
        self._fingerprint.cancel_enroll()
