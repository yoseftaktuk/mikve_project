from __future__ import annotations

import asyncio

from app import fingerprint as fp
from app.fingerprint import FingerprintController, fingerprint_enabled
from app.hardware import MockHardwareAdapter


class FakeSensor:
    """Stand-in for pyfingerprint's PyFingerprint over a real AS608."""

    def __init__(
        self,
        *,
        images: list[bool] | None = None,
        search_results: list[tuple[int, int]] | None = None,
        store_slot: int = 7,
        compare_score: int = 120,
    ) -> None:
        self._images = list(images or [])
        self._search_results = list(search_results or [(-1, 0)])
        self._store_slot = store_slot
        self._compare_score = compare_score
        self.converted_buffers: list[int] = []
        self.deleted_slots: list[int] = []

    def verifyPassword(self) -> bool:  # noqa: N802 - pyfingerprint API
        return True

    def getTemplateCount(self) -> int:  # noqa: N802 - pyfingerprint API
        return 3

    def readImage(self) -> bool:  # noqa: N802 - pyfingerprint API
        if not self._images:
            return False
        return self._images.pop(0)

    def convertImage(self, buffer: int) -> None:  # noqa: N802 - pyfingerprint API
        self.converted_buffers.append(buffer)

    def searchTemplate(self) -> tuple[int, int]:  # noqa: N802 - pyfingerprint API
        if len(self._search_results) > 1:
            return self._search_results.pop(0)
        return self._search_results[0]

    def compareCharacteristics(self) -> int:  # noqa: N802 - pyfingerprint API
        return self._compare_score

    def createTemplate(self) -> None:  # noqa: N802 - pyfingerprint API
        return None

    def storeTemplate(self) -> int:  # noqa: N802 - pyfingerprint API
        return self._store_slot

    def deleteTemplate(self, slot: int) -> None:  # noqa: N802 - pyfingerprint API
        self.deleted_slots.append(int(slot))


def build_controller(sensor: FakeSensor, *, loop: asyncio.AbstractEventLoop, steps: list, matches: list):
    """Controller wired to a fake sensor, recording progress and identify calls."""

    async def on_identified(slot: int, confidence: int) -> None:
        matches.append((slot, confidence))

    async def on_unmatched() -> None:
        matches.append(None)

    async def on_progress(session_id: str, step: str, slot: int | None) -> None:
        steps.append((session_id, step, slot))

    controller = FingerprintController(
        serial_port="/dev/fake-fingerprint",
        on_identified=on_identified,
        on_unmatched=on_unmatched,
        on_progress=on_progress,
        loop=loop,
        capture_timeout_seconds=1.0,
        rescan_cooldown_seconds=0.2,
    )
    controller._sensor = sensor
    controller._connected = True
    return controller


def test_fingerprint_enabled_requires_a_port():
    assert fingerprint_enabled("") is False
    assert fingerprint_enabled("   ") is False
    assert fingerprint_enabled("/dev/serial0") is True


async def test_enroll_emits_two_capture_sequence():
    steps: list = []
    matches: list = []
    sensor = FakeSensor(images=[True, False, True], store_slot=11)
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=matches)

    result = await asyncio.to_thread(controller.enroll_sync, "sess-1")
    await asyncio.sleep(0.05)  # let the cross-thread progress callbacks run

    assert result == {"step": "stored", "slot": 11}
    assert [step for _sid, step, _slot in steps] == [
        "place_finger",
        "remove_finger",
        "place_again",
        "stored",
    ]
    assert steps[-1] == ("sess-1", "stored", 11)
    assert sensor.converted_buffers == [fp._BUFFER_A, fp._BUFFER_B]


async def test_enroll_reports_duplicate_without_storing():
    steps: list = []
    sensor = FakeSensor(images=[True], search_results=[(4, 95)])
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=[])

    result = await asyncio.to_thread(controller.enroll_sync, "sess-dup")
    await asyncio.sleep(0.05)

    assert result == {"step": "duplicate", "slot": 4}
    assert [step for _sid, step, _slot in steps] == ["place_finger", "duplicate"]


async def test_enroll_reports_mismatch_when_captures_differ():
    steps: list = []
    sensor = FakeSensor(images=[True, False, True], compare_score=0)
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=[])

    result = await asyncio.to_thread(controller.enroll_sync, "sess-mismatch")
    await asyncio.sleep(0.05)

    assert result == {"step": "mismatch", "slot": None}
    assert [step for _sid, step, _slot in steps][-1] == "mismatch"


async def test_enroll_times_out_when_no_finger_arrives():
    steps: list = []
    sensor = FakeSensor(images=[])
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=[])

    result = await asyncio.to_thread(controller.enroll_sync, "sess-timeout")
    await asyncio.sleep(0.05)

    assert result == {"step": "timeout", "slot": None}
    assert [step for _sid, step, _slot in steps] == ["place_finger", "timeout"]


async def test_enroll_can_be_cancelled_while_waiting():
    steps: list = []
    sensor = FakeSensor(images=[])
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=[])

    task = asyncio.create_task(asyncio.to_thread(controller.enroll_sync, "sess-cancel"))
    await asyncio.sleep(0.1)
    controller.cancel_enroll()
    result = await task
    await asyncio.sleep(0.05)

    assert result == {"step": "cancelled", "slot": None}


async def test_identify_loop_reports_match_then_no_match():
    steps: list = []
    matches: list = []
    sensor = FakeSensor(images=[True, False, True, False], search_results=[(3, 88), (-1, 0)])
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=steps, matches=matches)

    identify = asyncio.create_task(asyncio.to_thread(controller._identify_loop))
    for _ in range(40):
        await asyncio.sleep(0.05)
        if len(matches) >= 2:
            break
    controller.stop()
    await identify

    assert matches[:2] == [(3, 88), None]


async def test_identify_loop_pauses_during_enrollment():
    matches: list = []
    sensor = FakeSensor(images=[True, False])
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=[], matches=matches)
    controller._enroll_active.set()

    identify = asyncio.create_task(asyncio.to_thread(controller._identify_loop))
    await asyncio.sleep(0.3)
    controller.stop()
    await identify

    assert matches == []


async def test_mock_adapter_enrollment_walks_through_steps():
    steps: list = []

    async def on_progress(session_id: str, step: str, slot: int | None) -> None:
        steps.append((step, slot))

    adapter = MockHardwareAdapter(
        on_rfid_scan=None,
        on_cash_inserted=None,
        on_fingerprint_progress=on_progress,
    )

    result = await adapter.enroll_fingerprint("sess-mock")

    assert result["step"] == "stored"
    assert result["slot"] == 1
    assert [step for step, _slot in steps] == ["place_finger", "remove_finger", "place_again", "stored"]


async def test_mock_adapter_simulates_match_and_no_match():
    scans: list = []

    async def on_scan(slot: int, confidence: int) -> None:
        scans.append((slot, confidence))

    async def on_unmatched() -> None:
        scans.append(None)

    adapter = MockHardwareAdapter(
        on_rfid_scan=None,
        on_cash_inserted=None,
        on_fingerprint_scan=on_scan,
        on_fingerprint_unmatched=on_unmatched,
    )

    await adapter.simulate_fingerprint_scan(5)
    await adapter.simulate_fingerprint_scan(None)

    assert scans == [(5, 100), None]


async def test_mock_adapter_status_reports_fingerprint_reader():
    adapter = MockHardwareAdapter(on_rfid_scan=None, on_cash_inserted=None)
    status = await adapter.get_status()

    assert status.fingerprint_reader_connected is True


async def test_mock_adapter_delete_fingerprint_is_idempotent():
    adapter = MockHardwareAdapter(on_rfid_scan=None, on_cash_inserted=None)
    result = await adapter.enroll_fingerprint("sess-del")
    slot = int(result["slot"])  # type: ignore[arg-type]
    assert slot in adapter._enrolled_slots

    await adapter.delete_fingerprint(slot)
    await adapter.delete_fingerprint(slot)
    assert slot not in adapter._enrolled_slots


async def test_controller_delete_template_calls_sensor():
    sensor = FakeSensor()
    controller = build_controller(sensor, loop=asyncio.get_running_loop(), steps=[], matches=[])
    await asyncio.to_thread(controller.delete_template_sync, 9)
    assert sensor.deleted_slots == [9]
