"""HTTP routes for the hardware service."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status

from .schemas import (
    DoorOpenRequest,
    DoorOpenResponse,
    FingerprintDeleteRequest,
    FingerprintEnrollRequest,
    SimulateCashRequest,
    SimulateFingerprintRequest,
)
from .hardware import HardwareAdapter
from .settings import settings
from . import state

router = APIRouter()


def _hardware(detail: str = "hardware_unavailable") -> HardwareAdapter:
    """Return the running adapter, or 503 if startup has not finished."""
    try:
        return state.require_adapter()
    except RuntimeError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail) from None


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "service": settings.service_name, "mode": settings.hardware_mode}


@router.get("/status")
async def get_status():
    """Return whether RFID, coin acceptor, and door relay are connected."""
    st = await _hardware().get_status()
    return {
        "mode": st.mode,
        "rfid_reader_connected": st.rfid_reader_connected,
        "coin_acceptor_connected": st.coin_acceptor_connected,
        "door_relay_connected": st.door_relay_connected,
        "fingerprint_reader_connected": st.fingerprint_reader_connected,
    }


@router.post("/door/open", response_model=DoorOpenResponse)
async def open_door(req: DoorOpenRequest):
    """Unlock the door relay and return confirmation when the command succeeds.

    Success means the unlock pulse was started without error — not that a person
    walked through. Optional operation_id / attempt_id are echoed for saga correlation.
    """
    seconds = req.seconds or settings.door_unlock_seconds
    st = await _hardware("door_unavailable").get_status()
    if not st.door_relay_connected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="door_unavailable")

    # Adapters block for the full unlock hold; confirm acceptance immediately and run
    # the hold in the background so access-control's DOOR_OPEN_TIMEOUT_MS is meaningful.
    asyncio.create_task(
        state.open_door_task(seconds, operation_id=req.operation_id, attempt_id=req.attempt_id)
    )

    return DoorOpenResponse(
        operation_id=req.operation_id,
        status="confirmed",
        unlocked_for_seconds=seconds,
    )


@router.post("/fingerprint/enroll", status_code=status.HTTP_202_ACCEPTED)
async def enroll_fingerprint(req: FingerprintEnrollRequest):
    """Start a fingerprint enrollment; progress arrives as Redis events."""
    asyncio.create_task(state.enroll_task(req.session_id))
    return {"session_id": req.session_id, "status": "started"}


@router.post("/fingerprint/enroll/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_fingerprint_enroll():
    """Abort a running fingerprint enrollment."""
    await _hardware().cancel_enroll()
    return None


@router.post("/fingerprint/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fingerprint(req: FingerprintDeleteRequest):
    """Remove a stored fingerprint template from the sensor."""
    try:
        await _hardware().delete_fingerprint(req.slot)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    return None


@router.post("/dev/cash/insert", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def dev_cash(req: SimulateCashRequest):
    """Simulate cash insertion in mock mode only."""
    if settings.hardware_mode != "mock":
        return None
    await _hardware().simulate_cash_inserted(req.amount_cents)
    return None


@router.post("/dev/fingerprint/scan", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def dev_fingerprint(req: SimulateFingerprintRequest):
    """Simulate a fingerprint match (or no-match) in mock mode only."""
    if settings.hardware_mode != "mock":
        return None
    await _hardware().simulate_fingerprint_scan(req.slot)
    return None
