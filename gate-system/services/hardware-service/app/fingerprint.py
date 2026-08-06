from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

try:
    from pyfingerprint.pyfingerprint import PyFingerprint  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - package missing on hosts without the sensor
    PyFingerprint = None  # type: ignore[assignment]

# pyfingerprint char-buffer slots used while enrolling.
_BUFFER_A = 0x01
_BUFFER_B = 0x02

# Steps published to the dashboard while enrolling.
STEP_PLACE_FINGER = "place_finger"
STEP_REMOVE_FINGER = "remove_finger"
STEP_PLACE_AGAIN = "place_again"
STEP_STORED = "stored"
STEP_DUPLICATE = "duplicate"
STEP_MISMATCH = "mismatch"
STEP_FAILED = "failed"
STEP_CANCELLED = "cancelled"
STEP_TIMEOUT = "timeout"

TERMINAL_STEPS = frozenset(
    {STEP_STORED, STEP_DUPLICATE, STEP_MISMATCH, STEP_FAILED, STEP_CANCELLED, STEP_TIMEOUT}
)


def fingerprint_enabled(serial_port: str | None) -> bool:
    """Fingerprint support is opt-in via a non-empty serial port, like RFID."""
    return bool(serial_port and serial_port.strip())


class FingerprintController:
    """AS608/R307 reader: background identify loop plus blocking enrollment.

    The sensor speaks over a single UART, so identify and enroll never run at the
    same time: enrollment sets a flag that parks the identify loop, then owns the
    serial handle until it reaches a terminal step.
    """

    def __init__(
        self,
        *,
        serial_port: str,
        baudrate: int = 57600,
        on_identified: Callable[[int, int], Awaitable[None]],
        on_unmatched: Callable[[], Awaitable[None]],
        on_progress: Callable[[str, str, int | None], Awaitable[None]],
        loop: asyncio.AbstractEventLoop | None = None,
        capture_timeout_seconds: float = 20.0,
        rescan_cooldown_seconds: float = 3.0,
    ) -> None:
        self._serial_port = serial_port
        self._baudrate = baudrate
        self._on_identified = on_identified
        self._on_unmatched = on_unmatched
        self._on_progress = on_progress
        self._loop = loop or asyncio.get_event_loop()
        self._capture_timeout_seconds = capture_timeout_seconds
        self._rescan_cooldown_seconds = rescan_cooldown_seconds

        self._sensor: PyFingerprint | None = None
        self._sensor_lock = threading.RLock()
        self._stop = threading.Event()
        self._enroll_active = threading.Event()
        self._enroll_cancel = threading.Event()
        self._identify_thread: threading.Thread | None = None
        self._connected = False
        self._last_missing_log = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def enrolling(self) -> bool:
        return self._enroll_active.is_set()

    def start(self) -> None:
        """Start the identify thread (no-op when pyfingerprint is unavailable)."""
        if PyFingerprint is None:
            logger.warning("fingerprint_reader_disabled pyfingerprint not installed")
            return
        self._stop.clear()
        self._identify_thread = threading.Thread(
            target=self._identify_loop, name="fingerprint-identify", daemon=True
        )
        self._identify_thread.start()
        logger.info("fingerprint_reader_starting port=%s baud=%s", self._serial_port, self._baudrate)

    def stop(self) -> None:
        """Stop the identify thread and close the serial handle."""
        self._stop.set()
        self._enroll_cancel.set()
        if self._identify_thread and self._identify_thread.is_alive():
            self._identify_thread.join(timeout=3)
        self._identify_thread = None
        self._close_sensor()

    def cancel_enroll(self) -> None:
        """Ask a running enrollment to stop at its next checkpoint."""
        self._enroll_cancel.set()

    def delete_template_sync(self, slot: int) -> None:
        """Remove a stored template from the sensor. Idempotent if the slot is empty."""
        if self._enroll_active.is_set():
            raise RuntimeError("cannot_delete_while_enrolling")
        with self._sensor_lock:
            sensor = self._ensure_sensor()
            if sensor is None:
                raise RuntimeError("fingerprint_reader_unavailable")
            try:
                sensor.deleteTemplate(int(slot))
            except Exception as exc:
                # Some firmwares raise when the slot is already empty; treat as success.
                message = str(exc).lower()
                if "empty" in message or "not used" in message or "no finger" in message:
                    logger.info("fingerprint_delete_slot_already_empty slot=%s", slot)
                    return
                raise
            logger.info("fingerprint_template_deleted slot=%s", slot)

    def _dispatch(self, coro: Awaitable[None]) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _emit(self, session_id: str, step: str, slot: int | None = None) -> None:
        self._dispatch(self._on_progress(session_id, step, slot))

    def _close_sensor(self) -> None:
        with self._sensor_lock:
            sensor = self._sensor
            self._sensor = None
            self._connected = False
        if sensor is None:
            return
        # pyfingerprint has no close(); reach for its name-mangled serial handle.
        port = getattr(sensor, "_PyFingerprint__serial", None)
        try:
            if port is not None:
                port.close()
        except Exception:
            pass

    def _log_port_missing(self) -> None:
        now = time.time()
        if now - self._last_missing_log < 30.0:
            return
        self._last_missing_log = now
        logger.warning("fingerprint_reader_unavailable port=%s", self._serial_port)

    def _ensure_sensor(self) -> PyFingerprint | None:
        """Open the sensor if needed; returns None while the port is unusable."""
        with self._sensor_lock:
            if self._sensor is not None:
                return self._sensor

            if not os.path.exists(self._serial_port):
                self._connected = False
                self._log_port_missing()
                return None

            try:
                sensor = PyFingerprint(self._serial_port, self._baudrate, 0xFFFFFFFF, 0x00000000)
                if not sensor.verifyPassword():
                    raise RuntimeError("fingerprint sensor password mismatch")
            except Exception:
                self._connected = False
                logger.exception("fingerprint_reader_open_failed port=%s", self._serial_port)
                return None

            self._sensor = sensor
            self._connected = True
            logger.info(
                "fingerprint_reader_connected port=%s templates=%s",
                self._serial_port,
                sensor.getTemplateCount(),
            )
            return sensor

    def _identify_loop(self) -> None:
        """Continuously match presented fingers against stored templates."""
        while not self._stop.is_set():
            if self._enroll_active.is_set():
                time.sleep(0.2)
                continue

            sensor = self._ensure_sensor()
            if sensor is None:
                time.sleep(2)
                continue

            try:
                with self._sensor_lock:
                    if self._enroll_active.is_set():
                        continue
                    if not sensor.readImage():
                        time.sleep(0.1)
                        continue
                    sensor.convertImage(_BUFFER_A)
                    slot, confidence = sensor.searchTemplate()
            except Exception:
                logger.exception("fingerprint_identify_error port=%s", self._serial_port)
                self._close_sensor()
                time.sleep(2)
                continue

            if slot >= 0:
                logger.info("fingerprint_match slot=%s confidence=%s", slot, confidence)
                self._dispatch(self._on_identified(int(slot), int(confidence)))
            else:
                logger.info("fingerprint_no_match")
                self._dispatch(self._on_unmatched())

            self._wait_for_finger_removal(sensor, timeout_seconds=self._rescan_cooldown_seconds)

    def _wait_for_finger_removal(self, sensor: PyFingerprint, *, timeout_seconds: float) -> bool:
        """Block until the finger leaves the sensor (or the timeout elapses)."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._stop.is_set():
                return False
            try:
                with self._sensor_lock:
                    if not sensor.readImage():
                        return True
            except Exception:
                return False
            time.sleep(0.15)
        return False

    def _wait_for_image(self, sensor: PyFingerprint, session_id: str) -> str | None:
        """Wait for a finger to be readable; returns a terminal step on failure."""
        deadline = time.time() + self._capture_timeout_seconds
        while time.time() < deadline:
            if self._stop.is_set() or self._enroll_cancel.is_set():
                return STEP_CANCELLED
            try:
                with self._sensor_lock:
                    if sensor.readImage():
                        return None
            except Exception:
                logger.exception("fingerprint_enroll_read_error session=%s", session_id)
                return STEP_FAILED
            time.sleep(0.15)
        return STEP_TIMEOUT

    def enroll_sync(self, session_id: str) -> dict[str, object]:
        """Run two-capture enrollment, emitting progress; returns the final step.

        Called from a worker thread. Result shape: ``{"step": ..., "slot": int | None}``.
        """
        if self._enroll_active.is_set():
            return {"step": STEP_FAILED, "slot": None, "error": "enrollment_already_running"}

        self._enroll_cancel.clear()
        self._enroll_active.set()
        try:
            sensor = self._ensure_sensor()
            if sensor is None:
                self._emit(session_id, STEP_FAILED)
                return {"step": STEP_FAILED, "slot": None, "error": "reader_unavailable"}

            self._emit(session_id, STEP_PLACE_FINGER)
            failure = self._wait_for_image(sensor, session_id)
            if failure is not None:
                self._emit(session_id, failure)
                return {"step": failure, "slot": None}

            with self._sensor_lock:
                sensor.convertImage(_BUFFER_A)
                existing_slot, _confidence = sensor.searchTemplate()
            if existing_slot >= 0:
                logger.info("fingerprint_enroll_duplicate session=%s slot=%s", session_id, existing_slot)
                self._emit(session_id, STEP_DUPLICATE, int(existing_slot))
                return {"step": STEP_DUPLICATE, "slot": int(existing_slot)}

            self._emit(session_id, STEP_REMOVE_FINGER)
            self._wait_for_finger_removal(sensor, timeout_seconds=self._capture_timeout_seconds)
            if self._enroll_cancel.is_set():
                self._emit(session_id, STEP_CANCELLED)
                return {"step": STEP_CANCELLED, "slot": None}

            self._emit(session_id, STEP_PLACE_AGAIN)
            failure = self._wait_for_image(sensor, session_id)
            if failure is not None:
                self._emit(session_id, failure)
                return {"step": failure, "slot": None}

            with self._sensor_lock:
                sensor.convertImage(_BUFFER_B)
                if sensor.compareCharacteristics() == 0:
                    self._emit(session_id, STEP_MISMATCH)
                    return {"step": STEP_MISMATCH, "slot": None}
                sensor.createTemplate()
                slot = int(sensor.storeTemplate())

            logger.info("fingerprint_enrolled session=%s slot=%s", session_id, slot)
            self._emit(session_id, STEP_STORED, slot)
            return {"step": STEP_STORED, "slot": slot}
        except Exception as exc:
            logger.exception("fingerprint_enroll_failed session=%s", session_id)
            self._emit(session_id, STEP_FAILED)
            return {"step": STEP_FAILED, "slot": None, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self._enroll_cancel.clear()
            self._enroll_active.clear()
