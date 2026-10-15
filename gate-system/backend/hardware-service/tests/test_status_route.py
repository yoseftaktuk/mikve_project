from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import state
from app.hardware import MockHardwareAdapter
from app.routes import get_status


async def test_status_is_unavailable_before_adapter_starts():
    previous = state.adapter
    state.adapter = None
    try:
        with pytest.raises(HTTPException) as exc:
            await get_status()
        assert exc.value.status_code == 503
        assert exc.value.detail == "hardware_unavailable"
    finally:
        state.adapter = previous


async def test_status_reads_running_adapter():
    previous = state.adapter
    state.adapter = MockHardwareAdapter(on_rfid_scan=None, on_cash_inserted=None)
    try:
        body = await get_status()
    finally:
        state.adapter = previous

    assert body["mode"] == "mock"
    assert body["rfid_reader_connected"] is True
    assert body["coin_acceptor_connected"] is True
    assert body["door_relay_connected"] is True
    assert body["fingerprint_reader_connected"] is True
