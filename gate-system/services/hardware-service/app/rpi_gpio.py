from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# #region agent log
_AGENT_DEBUG_ENDPOINTS = (
    "http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358",
    "http://192.168.150.196:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358",
)
_AGENT_DEBUG_LOG_PATH = "/Users/natankatz/mikve_project/.cursor/debug-8d1e46.log"


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any] | None = None) -> None:
    """Emit one NDJSON debug line (logger + best-effort HTTP/file)."""
    payload = {
        "sessionId": "8d1e46",
        "runId": "coin-pre",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    logger.info("AGENT_DEBUG %s", json.dumps(payload, default=str))
    line = json.dumps(payload, default=str) + "\n"
    try:
        with open(_AGENT_DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    body = line.encode("utf-8")
    for url in _AGENT_DEBUG_ENDPOINTS:
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Debug-Session-Id": "8d1e46",
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.4)
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            continue


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
        # #region agent log
        self._debug_total_pulses = 0
        self._debug_bursts = 0
        self._debug_mapped = 0
        self._debug_unknown = 0
        self._debug_last_burst_pulses: int | None = None
        self._debug_last_shekels: float | None = None
        self._debug_last_unknown_pulses: int | None = None
        self._debug_last_pin_level: int | None = None
        self._debug_last_pulse_at: str | None = None
        # #endregion

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
            pin_level = int(GPIO.input(self._coin_pin))
        except Exception as exc:  # noqa: BLE001 - debug only
            pin_level = None
            _agent_log(
                "A",
                "rpi_gpio.py:start",
                "gpio_started_pin_read_failed",
                {"coin_pin": self._coin_pin, "error": f"{type(exc).__name__}: {exc}"},
            )
        else:
            self._debug_last_pin_level = pin_level
            _agent_log(
                "A",
                "rpi_gpio.py:start",
                "gpio_started",
                {
                    "coin_pin": self._coin_pin,
                    "door_pin": self._door_pin,
                    "pin_level": pin_level,
                    "edge": "FALLING",
                    "pull": "PUD_UP",
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
            # #region agent log
            self._debug_total_pulses += 1
            total = self._debug_total_pulses
            burst = self._coin_count
            self._debug_last_pulse_at = self._last_pulse_time.isoformat(timespec="milliseconds")
            # #endregion
        # #region agent log
        if total <= 30 or total % 25 == 0:
            _agent_log(
                "A",
                "rpi_gpio.py:_pulse_detected",
                "coin_pulse",
                {"total_pulses": total, "burst_count": burst, "coin_pin": self._coin_pin},
            )
        # #endregion

    def debug_coin_stats(self) -> dict[str, Any]:
        """Return live coin-pulse counters for /status debugging."""
        # #region agent log
        pin_level = self._debug_last_pin_level
        if GPIO is not None and self._gpio_ready:
            try:
                pin_level = int(GPIO.input(self._coin_pin))
                self._debug_last_pin_level = pin_level
            except Exception:  # noqa: BLE001 - debug only
                pass
        with self._coin_lock:
            pending = self._coin_count
            last_pulse = self._debug_last_pulse_at
        return {
            "coin_pin": self._coin_pin,
            "pin_level": pin_level,
            "pending_pulses": pending,
            "total_pulses": self._debug_total_pulses,
            "bursts": self._debug_bursts,
            "mapped": self._debug_mapped,
            "unknown": self._debug_unknown,
            "last_burst_pulses": self._debug_last_burst_pulses,
            "last_shekels": self._debug_last_shekels,
            "last_unknown_pulses": self._debug_last_unknown_pulses,
            "last_pulse_at": last_pulse,
        }
        # #endregion

    def sample_coin_pin(self, duration_s: float = 5.0) -> dict[str, Any]:
        """Busy-sample the coin pin to detect any level changes during insertion."""
        # #region agent log
        if GPIO is None or not self._gpio_ready:
            return {"error": "gpio_not_ready"}

        duration_s = max(0.5, min(duration_s, 10.0))
        start_pulses = self._debug_total_pulses
        start = time.time()
        samples = 0
        highs = 0
        lows = 0
        falling = 0
        rising = 0
        prev: int | None = None
        while time.time() - start < duration_s:
            level = int(GPIO.input(self._coin_pin))
            samples += 1
            if level:
                highs += 1
            else:
                lows += 1
            if prev is not None and level != prev:
                if prev == 1 and level == 0:
                    falling += 1
                elif prev == 0 and level == 1:
                    rising += 1
            prev = level
            time.sleep(0.0005)

        result = {
            "duration_s": round(time.time() - start, 3),
            "coin_pin": self._coin_pin,
            "samples": samples,
            "highs": highs,
            "lows": lows,
            "falling_edges": falling,
            "rising_edges": rising,
            "event_pulses_during_sample": self._debug_total_pulses - start_pulses,
            "final_level": prev,
            "stats_after": self.debug_coin_stats(),
        }
        _agent_log("E", "rpi_gpio.py:sample_coin_pin", "coin_pin_sample", result)
        return result
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

        # #region agent log
        self._debug_bursts += 1
        self._debug_last_burst_pulses = pulses
        # #endregion
        shekels = pulses_to_shekels(pulses)
        if shekels is None:
            # #region agent log
            self._debug_unknown += 1
            self._debug_last_unknown_pulses = pulses
            _agent_log(
                "B",
                "rpi_gpio.py:_get_coin_if_ready",
                "unknown_coin_pulses",
                {"pulses": pulses, "bursts": self._debug_bursts, "unknown": self._debug_unknown},
            )
            # #endregion
            return None
        # #region agent log
        self._debug_mapped += 1
        self._debug_last_shekels = shekels
        _agent_log(
            "B",
            "rpi_gpio.py:_get_coin_if_ready",
            "coin_mapped",
            {"pulses": pulses, "shekels": shekels, "mapped": self._debug_mapped},
        )
        # #endregion
        logger.info("coin_detected pulses=%s shekels=%s", pulses, shekels)
        return shekels

    def _poll_loop(self) -> None:
        """Poll for completed coins and invoke the cash callback."""
        # #region agent log
        last_level_log = 0.0
        idle_samples = 0
        # #endregion
        while not self._stop.is_set():
            try:
                # #region agent log
                now = time.time()
                if (
                    idle_samples < 8
                    and now - last_level_log >= 5.0
                    and GPIO is not None
                    and self._gpio_ready
                ):
                    try:
                        level = int(GPIO.input(self._coin_pin))
                        self._debug_last_pin_level = level
                        if self._debug_total_pulses == 0:
                            _agent_log(
                                "A",
                                "rpi_gpio.py:_poll_loop",
                                "coin_pin_idle_sample",
                                {
                                    "coin_pin": self._coin_pin,
                                    "pin_level": level,
                                    "total_pulses": 0,
                                },
                            )
                            idle_samples += 1
                    except Exception:  # noqa: BLE001 - debug only
                        pass
                    last_level_log = now
                # #endregion
                shekels = self._get_coin_if_ready()
                if shekels is not None:
                    # #region agent log
                    _agent_log(
                        "C",
                        "rpi_gpio.py:_poll_loop",
                        "cash_callback_scheduled",
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
