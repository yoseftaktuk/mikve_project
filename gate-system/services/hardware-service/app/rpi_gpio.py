from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# #region agent log
_DEBUG_LOG_PATH = "/Users/natankatz/mikve_project/.cursor/debug-359384.log"
_DEBUG_INGEST = "http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358"
_DEBUG_INGEST_LAN = os.environ.get(
    "DEBUG_INGEST_URL",
    "http://192.168.150.196:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358",
)


def _agent_dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    """Session debug probe: file + HTTP ingest + logger (Pi-safe)."""
    payload = {
        "sessionId": "359384",
        "runId": "coin-pre",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, ensure_ascii=True)
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    body = line.encode("utf-8")
    for url in (_DEBUG_INGEST, _DEBUG_INGEST_LAN):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Debug-Session-Id": "359384",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.4).read()
        except Exception:
            pass
    logger.warning("AGENT_DEBUG %s", line)


# #endregion

try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - package missing on non-ARM hosts
    GPIO = None  # type: ignore[assignment]
except RuntimeError:  # pragma: no cover - installed but host is not usable as Pi GPIO
    GPIO = None  # type: ignore[assignment]

try:
    import serial  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]


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
    """BCM GPIO controller for coin input and door relay output."""

    def __init__(
        self,
        *,
        coin_pin: int,
        door_pin: int,
        on_cash_shekels: Callable[[float], Awaitable[None]],
        on_rfid_uid: Callable[[str], Awaitable[None]] | None = None,
        rfid_serial_port: str | None = None,
        rfid_baudrate: int = 9600,
        door_relay_idle_high: bool = False,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._coin_pin = coin_pin
        self._door_pin = door_pin
        self._on_cash_shekels = on_cash_shekels
        self._on_rfid_uid = on_rfid_uid
        self._rfid_serial_port = rfid_serial_port
        self._rfid_baudrate = rfid_baudrate
        self._door_relay_idle_high = door_relay_idle_high
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
        """GPIO level driven while the door should stay locked."""
        return GPIO.HIGH if self._door_relay_idle_high else GPIO.LOW

    def _apply_door_idle(self) -> None:
        """Drive the door pin (same electrical state as IN1 connected)."""
        GPIO.setup(self._door_pin, GPIO.OUT, initial=self._door_idle_level)

    def _float_door_pin(self) -> None:
        """Release the door pin (same electrical state as unplugging IN1)."""
        GPIO.setup(self._door_pin, GPIO.IN, pull_up_down=GPIO.PUD_OFF)

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
        self._apply_door_idle()

    def start(self) -> None:
        """Configure GPIO pins and start coin/RFID listener threads."""
        if GPIO is None:
            raise RuntimeError(
                "GPIO backend unavailable. Use rpi-lgpio on the Pi and start with "
                "docker compose -f docker-compose.yml -f deploy/docker-compose.pi.yml up -d --build"
            )

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

            self._release_gpio_pins([self._coin_pin, self._door_pin])
            GPIO.setmode(GPIO.BCM)
            try:
                self._configure_gpio_pins()
            except Exception as retry_exc:
                raise RuntimeError(
                    f"GPIO pin busy (coin={self._coin_pin}, door={self._door_pin}). "
                    "Stop duplicate hardware-service containers or other GPIO apps on the Pi."
                ) from retry_exc

        GPIO.add_event_detect(self._coin_pin, GPIO.FALLING, callback=self._pulse_detected, bouncetime=5)
        self._gpio_ready = True
        # #region agent log
        try:
            coin_level = int(GPIO.input(self._coin_pin))
        except Exception as exc:
            coin_level = f"err:{type(exc).__name__}"
        _agent_dbg(
            "E",
            "rpi_gpio.py:start",
            "gpio_started",
            {
                "coin_pin": self._coin_pin,
                "door_pin": self._door_pin,
                "gpio_ready": True,
                "coin_level": coin_level,
                "edge": "FALLING",
                "bouncetime_ms": 5,
            },
        )
        # #endregion
        logger.info(
            "gpio_started coin_pin=%s door_pin=%s door_idle=%s unlock_mode=float",
            self._coin_pin,
            self._door_pin,
            "HIGH" if self._door_relay_idle_high else "LOW",
        )

        self._stop.clear()
        self._listener_thread = threading.Thread(target=self._poll_loop, name="coin-listener", daemon=True)
        self._listener_thread.start()

        if self._on_rfid_uid and self._rfid_enabled(self._rfid_serial_port) and serial is not None:
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
        """Unlock by floating the door pin (like unplugging IN1), then restore idle OUTPUT."""
        if GPIO is None or not self._gpio_ready:
            raise RuntimeError("GPIO is not initialized")

        with self._door_lock:
            try:
                logger.info(
                    "door_open pin=%s seconds=%s mode=float idle=%s",
                    self._door_pin,
                    seconds,
                    "HIGH" if self._door_relay_idle_high else "LOW",
                )
                self._float_door_pin()
                time.sleep(seconds)
            finally:
                self._apply_door_idle()
                logger.info("door_closed pin=%s mode=idle_output", self._door_pin)

    def _pulse_detected(self, _channel: Any) -> None:
        """Count a falling-edge pulse from the coin acceptor."""
        with self._coin_lock:
            self._coin_count += 1
            self._last_pulse_time = datetime.now()
            count = self._coin_count
        # #region agent log
        if count <= 3 or count % 5 == 0:
            _agent_dbg(
                "A",
                "rpi_gpio.py:_pulse_detected",
                "pulse_edge",
                {"channel": _channel, "coin_count": count, "coin_pin": self._coin_pin},
            )
        # #endregion

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
        # #region agent log
        _agent_dbg(
            "B",
            "rpi_gpio.py:_get_coin_if_ready",
            "coin_settled",
            {
                "pulses": pulses,
                "shekels": shekels,
                "mapped": shekels is not None,
                "settle_ms": 200,
            },
        )
        # #endregion
        if shekels is None:
            return None
        logger.info("coin_detected pulses=%s shekels=%s", pulses, shekels)
        return shekels

    def _poll_loop(self) -> None:
        """Poll for completed coins and invoke the cash callback."""
        ticks = 0
        while not self._stop.is_set():
            try:
                ticks += 1
                # #region agent log
                if ticks == 1 or ticks % 200 == 0:
                    level: Any = None
                    try:
                        if GPIO is not None and self._gpio_ready:
                            level = int(GPIO.input(self._coin_pin))
                    except Exception as exc:
                        level = f"err:{type(exc).__name__}"
                    _agent_dbg(
                        "A",
                        "rpi_gpio.py:_poll_loop",
                        "coin_pin_sample",
                        {
                            "ticks": ticks,
                            "coin_pin": self._coin_pin,
                            "level": level,
                            "pending_count": self._coin_count,
                            "listener_alive": True,
                        },
                    )
                # #endregion
                shekels = self._get_coin_if_ready()
                if shekels is not None:
                    # #region agent log
                    _agent_dbg(
                        "C",
                        "rpi_gpio.py:_poll_loop",
                        "dispatch_cash_callback",
                        {"shekels": shekels},
                    )
                    # #endregion
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
