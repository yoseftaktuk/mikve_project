from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# #region agent log
def _agent_dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": "359384",
        "runId": "post-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    logger.warning("AGENT_DEBUG %s", payload)
    try:
        import json
        import urllib.request
        body = json.dumps(payload).encode()
        for host in ("http://host.docker.internal:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358",
                     "http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358"):
            try:
                req = urllib.request.Request(
                    host,
                    data=body,
                    headers={"Content-Type": "application/json", "X-Debug-Session-Id": "359384"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=1)
                break
            except Exception:
                continue
    except Exception:
        pass
# #endregion

# #region agent log
def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return None

def _gpio_dists() -> list[str]:
    try:
        import importlib.metadata as md
        return sorted({
            f"{d.metadata['Name']}:{d.version}"
            for d in md.distributions()
            if d.metadata["Name"] in ("RPi.GPIO", "rpi-lgpio", "lgpio")
        })
    except Exception as e:
        return [f"error:{type(e).__name__}:{e}"]

_agent_dbg("D", "rpi_gpio.py:import", "before_RPi_GPIO_import", {
    "machine": platform.machine(),
    "system": platform.system(),
    "platform": platform.platform(),
    "device_tree_model": _read_text("/proc/device-tree/model"),
    "gpiochip0_exists": __import__("os").path.exists("/dev/gpiochip0"),
    "gpiomem_exists": __import__("os").path.exists("/dev/gpiomem"),
    "dev_mounted": __import__("os").path.isdir("/dev"),
    "gpio_dists": _gpio_dists(),
})
# #endregion

try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
    # #region agent log
    _agent_dbg("E", "rpi_gpio.py:import", "RPi_GPIO_import_ok", {
        "gpio_module": getattr(GPIO, "__name__", None),
        "gpio_file": getattr(GPIO, "__file__", None),
    })
    # #endregion
except ImportError as e:  # pragma: no cover - package missing on non-ARM hosts
    GPIO = None  # type: ignore[assignment]
    # #region agent log
    _agent_dbg("E", "rpi_gpio.py:import", "RPi_GPIO_ImportError", {"type": type(e).__name__, "msg": str(e)})
    # #endregion
except RuntimeError as e:  # pragma: no cover - installed but host is not usable as Pi GPIO
    GPIO = None  # type: ignore[assignment]
    # #region agent log
    _agent_dbg("E", "rpi_gpio.py:import", "RPi_GPIO_RuntimeError_caught", {"type": type(e).__name__, "msg": str(e)})
    # #endregion

try:
    import serial  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]

# #region agent log
_agent_dbg("B", "rpi_gpio.py:import", "module_loaded", {"gpio_available": GPIO is not None, "serial_available": serial is not None})
# #endregion

def pulses_to_shekels(pulses: int) -> float | None:
    """Map coin-acceptor pulse counts to shekel amounts."""
    if pulses == 10:
        return 5.0
    if pulses == 15:
        return 10.0
    if pulses == 5:
        return 1.0
    if pulses == 1:
        return 0.1
    logger.warning("unknown_coin_pulses pulses=%s", pulses)
    return None


class RpiGpioController:
    """BCM GPIO controller for coin input (pin 17) and door relay output (pin 22)."""

    def __init__(
        self,
        *,
        coin_pin: int,
        door_pin: int,
        on_cash_shekels: Callable[[float], Awaitable[None]],
        on_rfid_uid: Callable[[str], Awaitable[None]] | None = None,
        rfid_serial_port: str | None = None,
        rfid_baudrate: int = 9600,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._coin_pin = coin_pin
        self._door_pin = door_pin
        self._on_cash_shekels = on_cash_shekels
        self._on_rfid_uid = on_rfid_uid
        self._rfid_serial_port = rfid_serial_port
        self._rfid_baudrate = rfid_baudrate
        self._loop = loop or asyncio.get_event_loop()

        self._coin_count = 0
        self._last_pulse_time: datetime | None = None
        self._coin_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._rfid_thread: threading.Thread | None = None
        self._door_lock = threading.Lock()
        self._gpio_ready = False

    @property
    def gpio_ready(self) -> bool:
        return self._gpio_ready

    def start(self) -> None:
        """Configure GPIO pins and start coin/RFID listener threads."""
        if GPIO is None:
            raise RuntimeError(
                "GPIO backend unavailable. Use rpi-lgpio on the Pi and start with "
                "docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build"
            )

        # #region agent log
        _agent_dbg("E", "rpi_gpio.py:start", "gpio_start_begin", {
            "coin_pin": self._coin_pin,
            "door_pin": self._door_pin,
        })
        # #endregion

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._coin_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self._door_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.add_event_detect(self._coin_pin, GPIO.FALLING, callback=self._pulse_detected, bouncetime=5)
        self._gpio_ready = True
        logger.info("gpio_started coin_pin=%s door_pin=%s", self._coin_pin, self._door_pin)

        # #region agent log
        _agent_dbg("E", "rpi_gpio.py:start", "gpio_start_ok", {"gpio_ready": self._gpio_ready})
        # #endregion

        self._stop.clear()
        self._listener_thread = threading.Thread(target=self._poll_loop, name="coin-listener", daemon=True)
        self._listener_thread.start()

        if self._on_rfid_uid and self._rfid_serial_port and serial is not None:
            self._rfid_thread = threading.Thread(target=self._rfid_loop, name="rfid-listener", daemon=True)
            self._rfid_thread.start()

    def stop(self) -> None:
        """Stop listener threads and release GPIO resources."""
        self._stop.set()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2)
        if self._rfid_thread and self._rfid_thread.is_alive():
            self._rfid_thread.join(timeout=2)
        if GPIO is not None and self._gpio_ready:
            GPIO.cleanup()
            self._gpio_ready = False
            logger.info("gpio_stopped")

    def open_door_sync(self, seconds: int) -> None:
        """Hold the door relay HIGH for the given number of seconds."""
        if GPIO is None or not self._gpio_ready:
            raise RuntimeError("GPIO is not initialized")

        with self._door_lock:
            logger.info("door_open pin=%s seconds=%s", self._door_pin, seconds)
            GPIO.output(self._door_pin, GPIO.HIGH)
            time.sleep(seconds)
            GPIO.output(self._door_pin, GPIO.LOW)
            logger.info("door_closed pin=%s", self._door_pin)

    def _pulse_detected(self, _channel: Any) -> None:
        """Count a falling-edge pulse from the coin acceptor."""
        with self._coin_lock:
            self._coin_count += 1
            self._last_pulse_time = datetime.now()

    def _get_coin_if_ready(self) -> float | None:
        """Return shekel value once pulse bursts settle (~200ms quiet)."""
        with self._coin_lock:
            if self._last_pulse_time is None:
                return None

            delta = datetime.now() - self._last_pulse_time
            if delta <= timedelta(milliseconds=200):
                return None

            pulses = self._coin_count
            self._coin_count = 0
            self._last_pulse_time = None

        shekels = pulses_to_shekels(pulses)
        if shekels is None:
            return None
        logger.info("coin_detected pulses=%s shekels=%s", pulses, shekels)
        return shekels

    def _poll_loop(self) -> None:
        """Poll for completed coins and invoke the cash callback."""
        while not self._stop.is_set():
            try:
                shekels = self._get_coin_if_ready()
                if shekels is not None:
                    asyncio.run_coroutine_threadsafe(self._on_cash_shekels(shekels), self._loop)
            except Exception:
                logger.exception("coin_poll_error")
            time.sleep(0.05)

    def _rfid_loop(self) -> None:
        """Read chip UIDs from the USB serial RFID reader."""
        assert self._on_rfid_uid is not None
        assert self._rfid_serial_port is not None
        assert serial is not None

        while not self._stop.is_set():
            try:
                with serial.Serial(self._rfid_serial_port, self._rfid_baudrate, timeout=0.2) as port:
                    logger.info("rfid_reader_connected port=%s", self._rfid_serial_port)
                    while not self._stop.is_set():
                        raw = port.readline()
                        if not raw:
                            continue
                        uid = raw.decode(errors="ignore").strip()
                        if uid:
                            logger.info("rfid_scan uid=%s", uid)
                            asyncio.run_coroutine_threadsafe(self._on_rfid_uid(uid), self._loop)
            except Exception:
                # #region agent log
                import glob
                import os
                _agent_dbg("RFID-A,B,C", "rpi_gpio.py:_rfid_loop", "rfid_serial_open_failed", {
                    "configured_port": self._rfid_serial_port,
                    "configured_port_exists": os.path.exists(self._rfid_serial_port),
                    "serial_devices": sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")),
                    "dev_is_mounted": os.path.isdir("/dev"),
                })
                # #endregion
                logger.exception("rfid_reader_error port=%s", self._rfid_serial_port)
                time.sleep(2)
