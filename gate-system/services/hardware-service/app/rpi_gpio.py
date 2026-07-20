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
        door_relay_active_high: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._coin_pin = coin_pin
        self._door_pin = door_pin
        self._on_cash_shekels = on_cash_shekels
        self._on_rfid_uid = on_rfid_uid
        self._rfid_serial_port = rfid_serial_port
        self._rfid_baudrate = rfid_baudrate
        self._door_relay_active_high = door_relay_active_high
        self._loop = loop or asyncio.get_event_loop()

        self._coin_count = 0
        self._last_pulse_time: datetime | None = None
        self._coin_lock = threading.Lock()
        self._stop = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._rfid_thread: threading.Thread | None = None
        self._door_lock = threading.Lock()
        self._gpio_ready = False
        self._rfid_connected = False

    @property
    def _door_idle_level(self) -> int:
        """GPIO level when the door should stay locked."""
        return GPIO.HIGH if not self._door_relay_active_high else GPIO.LOW

    @property
    def _door_unlock_level(self) -> int:
        """GPIO level that energizes the unlock path on the relay."""
        return GPIO.LOW if not self._door_relay_active_high else GPIO.HIGH

    @property
    def rfid_connected(self) -> bool:
        return self._rfid_connected

    @staticmethod
    def _serial_devices() -> list[str]:
        import glob

        return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))

    @staticmethod
    def _rfid_enabled(rfid_serial_port: str | None) -> bool:
        return bool(rfid_serial_port and rfid_serial_port.strip())

    @property
    def gpio_ready(self) -> bool:
        return self._gpio_ready

    def _release_gpio_pins(self, pins: list[int]) -> dict[str, Any]:
        """Best-effort release of BCM pins before (re)claiming them."""
        result: dict[str, Any] = {"pins": pins}
        try:
            GPIO.cleanup()
            result["cleanup_called"] = True
        except Exception as exc:
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"

        try:
            import lgpio

            chip = lgpio.gpiochip_open(0)
            freed: list[int] = []
            errors: dict[int, str] = {}
            for pin in pins:
                try:
                    lgpio.gpio_free(chip, pin)
                    freed.append(pin)
                except Exception as exc:
                    errors[pin] = f"{type(exc).__name__}: {exc}"
            lgpio.gpiochip_close(chip)
            result["lgpio_freed"] = freed
            if errors:
                result["lgpio_errors"] = errors
        except Exception as exc:
            result["lgpio_chip_error"] = f"{type(exc).__name__}: {exc}"

        return result

    def _configure_gpio_pins(self) -> None:
        GPIO.setup(self._coin_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self._door_pin, GPIO.OUT, initial=self._door_idle_level)

    def start(self) -> None:
        """Configure GPIO pins and start coin/RFID listener threads."""
        if GPIO is None:
            raise RuntimeError(
                "GPIO backend unavailable. Use rpi-lgpio on the Pi and start with "
                "docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build"
            )

        # #region agent log
        _agent_dbg("RELAY-A,B", "rpi_gpio.py:start", "gpio_start_begin", {
            "coin_pin": self._coin_pin,
            "door_pin": self._door_pin,
            "same_pin": self._coin_pin == self._door_pin,
            "door_relay_active_high": self._door_relay_active_high,
            "door_idle_level": "HIGH" if not self._door_relay_active_high else "LOW",
            "door_unlock_level": "LOW" if not self._door_relay_active_high else "HIGH",
        })
        # #endregion

        if self._coin_pin == self._door_pin:
            raise RuntimeError(
                f"COIN_ACCEPTOR_GPIO_PIN and DOOR_RELAY_GPIO_PIN must differ (both={self._coin_pin})"
            )

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)

        try:
            self._configure_gpio_pins()
        except Exception as exc:
            if "busy" not in str(exc).lower():
                raise

            release_result = self._release_gpio_pins([self._coin_pin, self._door_pin])
            # #region agent log
            _agent_dbg("GPIO-A,B", "rpi_gpio.py:start", "gpio_busy_retry_after_release", {
                "error": f"{type(exc).__name__}: {exc}",
                "coin_pin": self._coin_pin,
                "door_pin": self._door_pin,
                "release_result": release_result,
            })
            # #endregion

            GPIO.setmode(GPIO.BCM)
            try:
                self._configure_gpio_pins()
            except Exception as retry_exc:
                # #region agent log
                _agent_dbg("GPIO-B", "rpi_gpio.py:start", "gpio_busy_retry_failed", {
                    "error": f"{type(retry_exc).__name__}: {retry_exc}",
                    "coin_pin": self._coin_pin,
                    "door_pin": self._door_pin,
                    "hint": "Another process/container may hold these GPIO lines. Run: docker ps && sudo gpioinfo gpiochip0",
                })
                # #endregion
                raise RuntimeError(
                    f"GPIO pin busy (coin={self._coin_pin}, door={self._door_pin}). "
                    "Stop duplicate hardware-service containers or other GPIO apps on the Pi."
                ) from retry_exc

        GPIO.add_event_detect(self._coin_pin, GPIO.FALLING, callback=self._pulse_detected, bouncetime=5)
        self._gpio_ready = True
        logger.info(
            "gpio_started coin_pin=%s door_pin=%s door_relay_active_high=%s",
            self._coin_pin,
            self._door_pin,
            self._door_relay_active_high,
        )

        # #region agent log
        try:
            idle_readback = int(GPIO.input(self._door_pin))
        except Exception as exc:
            idle_readback = f"error:{type(exc).__name__}"
        _agent_dbg("RELAY-A,B", "rpi_gpio.py:start", "gpio_start_ok", {
            "gpio_ready": self._gpio_ready,
            "door_pin_readback": idle_readback,
            "door_relay_active_high": self._door_relay_active_high,
        })
        # #endregion

        self._stop.clear()
        self._listener_thread = threading.Thread(target=self._poll_loop, name="coin-listener", daemon=True)
        self._listener_thread.start()

        if self._on_rfid_uid and self._rfid_enabled(self._rfid_serial_port) and serial is not None:
            # #region agent log
            import os

            _agent_dbg("RFID-A,B,C", "rpi_gpio.py:start", "rfid_thread_start", {
                "configured_port": self._rfid_serial_port,
                "configured_port_exists": os.path.exists(self._rfid_serial_port or ""),
                "serial_devices": self._serial_devices(),
            })
            # #endregion
            self._rfid_thread = threading.Thread(target=self._rfid_loop, name="rfid-listener", daemon=True)
            self._rfid_thread.start()
        elif self._on_rfid_uid and not self._rfid_enabled(self._rfid_serial_port):
            logger.info("rfid_reader_disabled no RFID_SERIAL_PORT configured")

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
        """Unlock by driving the door pin (default: LOW / 0V), then restore idle level."""
        if GPIO is None or not self._gpio_ready:
            raise RuntimeError("GPIO is not initialized")

        unlock = self._door_unlock_level
        idle = self._door_idle_level
        with self._door_lock:
            before = int(GPIO.input(self._door_pin))
            logger.info(
                "door_open pin=%s seconds=%s active_high=%s unlock_level=%s",
                self._door_pin,
                seconds,
                self._door_relay_active_high,
                "LOW" if unlock == GPIO.LOW else "HIGH",
            )
            # Default (active_high=False): unlock = LOW → no voltage on the pin while open.
            GPIO.output(self._door_pin, unlock)
            during = int(GPIO.input(self._door_pin))
            # #region agent log
            _agent_dbg("RELAY-A,B,C", "rpi_gpio.py:open_door_sync", "door_unlock_asserted", {
                "door_pin": self._door_pin,
                "seconds": seconds,
                "active_high": self._door_relay_active_high,
                "level_before": before,
                "level_during": during,
                "expected_during": int(unlock),
            })
            # #endregion
            time.sleep(seconds)
            GPIO.output(self._door_pin, idle)
            after = int(GPIO.input(self._door_pin))
            # #region agent log
            _agent_dbg("RELAY-A,B,C", "rpi_gpio.py:open_door_sync", "door_idle_restored", {
                "door_pin": self._door_pin,
                "level_after": after,
                "expected_after": int(idle),
            })
            # #endregion
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

        import os

        missing_log_interval = 30.0
        last_missing_log = 0.0

        while not self._stop.is_set():
            port_path = self._rfid_serial_port
            if not os.path.exists(port_path):
                self._rfid_connected = False
                now = time.time()
                if now - last_missing_log >= missing_log_interval:
                    devices = self._serial_devices()
                    # #region agent log
                    _agent_dbg("RFID-A,B,C", "rpi_gpio.py:_rfid_loop", "rfid_serial_unavailable", {
                        "configured_port": port_path,
                        "configured_port_exists": False,
                        "serial_devices": devices,
                        "dev_is_mounted": os.path.isdir("/dev"),
                        "hint": "Plug in the reader or set RFID_SERIAL_PORT= to disable RFID",
                    })
                    # #endregion
                    logger.warning(
                        "rfid_reader_unavailable port=%s available_serial_devices=%s",
                        port_path,
                        devices or "none",
                    )
                    last_missing_log = now
                time.sleep(2)
                continue

            try:
                with serial.Serial(port_path, self._rfid_baudrate, timeout=0.2) as port:
                    self._rfid_connected = True
                    logger.info("rfid_reader_connected port=%s", port_path)
                    while not self._stop.is_set():
                        raw = port.readline()
                        if not raw:
                            continue
                        uid = raw.decode(errors="ignore").strip()
                        if uid:
                            logger.info("rfid_scan uid=%s", uid)
                            asyncio.run_coroutine_threadsafe(self._on_rfid_uid(uid), self._loop)
            except OSError as exc:
                self._rfid_connected = False
                if getattr(exc, "errno", None) == 2:
                    now = time.time()
                    if now - last_missing_log >= missing_log_interval:
                        devices = self._serial_devices()
                        # #region agent log
                        _agent_dbg("RFID-A,B,C", "rpi_gpio.py:_rfid_loop", "rfid_serial_open_failed", {
                            "configured_port": port_path,
                            "configured_port_exists": False,
                            "serial_devices": devices,
                            "dev_is_mounted": os.path.isdir("/dev"),
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        # #endregion
                        logger.warning(
                            "rfid_reader_unavailable port=%s available_serial_devices=%s",
                            port_path,
                            devices or "none",
                        )
                        last_missing_log = now
                else:
                    logger.exception("rfid_reader_error port=%s", port_path)
                time.sleep(2)
            except Exception:
                self._rfid_connected = False
                logger.exception("rfid_reader_error port=%s", port_path)
                time.sleep(2)
