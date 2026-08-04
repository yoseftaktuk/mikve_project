import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status

from gate_shared.logging import configure_logging

from .fingerprint import STEP_STORED
from .hardware import MockHardwareAdapter, RpiHardwareAdapter
from .schemas import (
    DoorOpenRequest,
    DoorOpenResponse,
    FingerprintEnrollRequest,
    SimulateCashRequest,
    SimulateFingerprintRequest,
    SimulateRfidRequest,
)
from .settings import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Hardware Service",
    version="0.1.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

redis_client: redis.Redis | None = None
adapter = None


async def _publish(channel: str, event: dict) -> None:
    """Publish a JSON event to a Redis pub/sub channel."""
    if redis_client is None:
        return
    try:
        # A hung publish would silently stall coin/RFID events, so fail loudly instead.
        await asyncio.wait_for(redis_client.publish(channel, json.dumps(event)), timeout=3.0)
    except Exception:
        logger.exception("redis_publish_failed channel=%s type=%s", channel, event.get("type"))
        raise


async def on_rfid_scan(uid: str) -> None:
    """Publish an rfid.scan event when a chip UID is read."""
    await _publish(
        "hardware.events",
        {
            "type": "rfid.scan",
            "uid": uid,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def on_cash_inserted(amount_cents: int) -> None:
    """Publish a cash.inserted event when a coin is accepted."""
    # #region agent log
    logger.info(
        "AGENT_DEBUG %s",
        json.dumps(
            {
                "sessionId": "8d1e46",
                "hypothesisId": "C",
                "location": "main.py:on_cash_inserted",
                "message": "publishing_cash_inserted",
                "data": {"amount_cents": amount_cents},
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ),
    )
    # #endregion
    await _publish(
        "hardware.events",
        {
            "type": "cash.inserted",
            "amount_cents": amount_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def on_fingerprint_scan(slot: int, confidence: int) -> None:
    """Publish a fingerprint.scan event when a stored template matches."""
    await _publish(
        "hardware.events",
        {
            "type": "fingerprint.scan",
            "slot": slot,
            "confidence": confidence,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def on_fingerprint_unmatched() -> None:
    """Publish a fingerprint.unmatched event for a finger with no stored template."""
    await _publish(
        "hardware.events",
        {"type": "fingerprint.unmatched", "ts": datetime.now(timezone.utc).isoformat()},
    )


async def on_fingerprint_progress(session_id: str, step: str, slot: int | None) -> None:
    """Publish an enrollment progress step so the dashboard can guide the user."""
    await _publish(
        "hardware.events",
        {
            "type": "fingerprint.enroll_progress",
            "session_id": session_id,
            "step": step,
            "slot": slot,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.on_event("startup")
async def startup() -> None:
    """Connect Redis and start the mock or Raspberry Pi hardware adapter."""
    global redis_client, adapter
    configure_logging(settings.service_name, settings.log_level)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    if settings.hardware_mode == "mock":
        adapter = MockHardwareAdapter(
            on_rfid_scan=on_rfid_scan,
            on_cash_inserted=on_cash_inserted,
            on_fingerprint_scan=on_fingerprint_scan,
            on_fingerprint_unmatched=on_fingerprint_unmatched,
            on_fingerprint_progress=on_fingerprint_progress,
        )
    else:
        adapter = RpiHardwareAdapter(
            on_rfid_scan=on_rfid_scan,
            on_cash_inserted=on_cash_inserted,
            coin_pin=settings.coin_acceptor_gpio_pin,
            door_pin=settings.door_relay_gpio_pin,
            door_unlock_seconds=settings.door_unlock_seconds,
            rfid_serial_port=settings.rfid_serial_port,
            rfid_baudrate=settings.rfid_baudrate,
            door_relay_idle_high=settings.door_relay_idle_high,
            on_fingerprint_scan=on_fingerprint_scan,
            on_fingerprint_unmatched=on_fingerprint_unmatched,
            on_fingerprint_progress=on_fingerprint_progress,
            fingerprint_serial_port=settings.fingerprint_serial_port,
            fingerprint_baudrate=settings.fingerprint_baudrate,
        )
    await adapter.start()
    logger.info("startup_complete mode=%s", settings.hardware_mode)


@app.on_event("shutdown")
async def shutdown() -> None:
    """Stop the hardware adapter and close the Redis connection."""
    global redis_client, adapter
    if adapter is not None:
        await adapter.stop()
        adapter = None
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": settings.service_name, "mode": settings.hardware_mode}


@app.get("/status")
async def get_status():
    """Return whether RFID, coin acceptor, and door relay are connected."""
    st = await adapter.get_status()
    payload = {
        "mode": st.mode,
        "rfid_reader_connected": st.rfid_reader_connected,
        "coin_acceptor_connected": st.coin_acceptor_connected,
        "door_relay_connected": st.door_relay_connected,
        "fingerprint_reader_connected": st.fingerprint_reader_connected,
    }
    # #region agent log
    get_coin_debug = getattr(adapter, "get_coin_debug", None)
    if callable(get_coin_debug):
        payload["coin_debug"] = get_coin_debug()
    # #endregion
    return payload


@app.post("/debug/coin-sample")
async def debug_coin_sample(duration_s: float = 5.0):
    """Busy-sample the coin pin while a coin is inserted (debug only)."""
    # #region agent log
    sample_fn = getattr(adapter, "sample_coin_pin", None)
    if not callable(sample_fn):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="coin sampling unavailable")
    result = await sample_fn(duration_s)
    return result
    # #endregion


async def _open_door_task(seconds: int, *, operation_id: str | None = None, attempt_id: str | None = None) -> None:
    """Unlock the door for the given seconds and publish door.opened."""
    try:
        await adapter.open_door(seconds=seconds)
        payload = {
            "type": "door.opened",
            "seconds": seconds,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if operation_id:
            payload["operation_id"] = operation_id
        if attempt_id:
            payload["attempt_id"] = attempt_id
        await _publish("hardware.events", payload)
    except Exception:
        logger.exception("door_open_failed seconds=%s operation_id=%s", seconds, operation_id)
        fail = {
            "type": "door.failed",
            "seconds": seconds,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if operation_id:
            fail["operation_id"] = operation_id
        if attempt_id:
            fail["attempt_id"] = attempt_id
        await _publish("hardware.events", fail)


@app.post("/door/open", response_model=DoorOpenResponse)
async def open_door(req: DoorOpenRequest):
    """Unlock the door relay and return confirmation when the command succeeds.

    Success means the unlock pulse was started without error — not that a person
    walked through. Optional operation_id / attempt_id are echoed for saga correlation.
    """
    seconds = req.seconds or settings.door_unlock_seconds
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="door_unavailable")
    st = await adapter.get_status()
    if not st.door_relay_connected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="door_unavailable")

    # Adapters block for the full unlock hold; confirm acceptance immediately and run
    # the hold in the background so access-control's DOOR_OPEN_TIMEOUT_MS is meaningful.
    asyncio.create_task(
        _open_door_task(seconds, operation_id=req.operation_id, attempt_id=req.attempt_id)
    )

    return DoorOpenResponse(
        operation_id=req.operation_id,
        status="confirmed",
        unlocked_for_seconds=seconds,
    )


async def _enroll_task(session_id: str) -> None:
    """Run enrollment and publish fingerprint.enrolled once a template is stored."""
    try:
        result = await adapter.enroll_fingerprint(session_id)
    except Exception:
        logger.exception("fingerprint_enroll_failed session=%s", session_id)
        await on_fingerprint_progress(session_id, "failed", None)
        return

    if result.get("step") != STEP_STORED:
        return

    await _publish(
        "hardware.events",
        {
            "type": "fingerprint.enrolled",
            "session_id": session_id,
            "slot": result.get("slot"),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.post("/fingerprint/enroll", status_code=status.HTTP_202_ACCEPTED)
async def enroll_fingerprint(req: FingerprintEnrollRequest):
    """Start a fingerprint enrollment; progress arrives as Redis events."""
    asyncio.create_task(_enroll_task(req.session_id))
    return {"session_id": req.session_id, "status": "started"}


@app.post("/fingerprint/enroll/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_fingerprint_enroll():
    """Abort a running fingerprint enrollment."""
    await adapter.cancel_enroll()
    return None


# Dev endpoints (mock mode)
@app.post("/dev/rfid/scan", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def dev_scan(req: SimulateRfidRequest):
    """Simulate an RFID scan in mock mode only."""
    if settings.hardware_mode != "mock":
        return None
    await adapter.simulate_rfid_scan(req.uid)
    return None


@app.post("/dev/cash/insert", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def dev_cash(req: SimulateCashRequest):
    """Simulate cash insertion in mock mode only."""
    if settings.hardware_mode != "mock":
        return None
    await adapter.simulate_cash_inserted(req.amount_cents)
    return None


@app.post("/dev/fingerprint/scan", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def dev_fingerprint(req: SimulateFingerprintRequest):
    """Simulate a fingerprint match (or no-match) in mock mode only."""
    if settings.hardware_mode != "mock":
        return None
    await adapter.simulate_fingerprint_scan(req.slot)
    return None
