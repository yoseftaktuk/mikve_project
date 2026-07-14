import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, status

from gate_shared.logging import configure_logging

from .hardware import MockHardwareAdapter, RpiHardwareAdapter
from .schemas import DoorOpenRequest, SimulateCashRequest, SimulateRfidRequest
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
    await redis_client.publish(channel, json.dumps(event))


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
    await _publish(
        "hardware.events",
        {
            "type": "cash.inserted",
            "amount_cents": amount_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.on_event("startup")
async def startup() -> None:
    """Connect Redis and start the mock or Raspberry Pi hardware adapter."""
    global redis_client, adapter
    configure_logging(settings.service_name, settings.log_level)
    # #region agent log
    logger.warning(
        "AGENT_DEBUG %s",
        {
            "sessionId": "359384",
            "runId": "pre-fix",
            "hypothesisId": "C",
            "location": "main.py:startup",
            "message": "hardware_mode_selected",
            "data": {"hardware_mode": settings.hardware_mode},
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        },
    )
    # #endregion
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    if settings.hardware_mode == "mock":
        adapter = MockHardwareAdapter(on_rfid_scan=on_rfid_scan, on_cash_inserted=on_cash_inserted)
    else:
        adapter = RpiHardwareAdapter(
            on_rfid_scan=on_rfid_scan,
            on_cash_inserted=on_cash_inserted,
            coin_pin=settings.coin_acceptor_gpio_pin,
            door_pin=settings.door_relay_gpio_pin,
            door_unlock_seconds=settings.door_unlock_seconds,
            rfid_serial_port=settings.rfid_serial_port,
            rfid_baudrate=settings.rfid_baudrate,
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
    return {
        "mode": st.mode,
        "rfid_reader_connected": st.rfid_reader_connected,
        "coin_acceptor_connected": st.coin_acceptor_connected,
        "door_relay_connected": st.door_relay_connected,
    }


async def _open_door_task(seconds: int) -> None:
    """Unlock the door for the given seconds and publish door.opened."""
    try:
        await adapter.open_door(seconds=seconds)
        await _publish(
            "hardware.events",
            {"type": "door.opened", "seconds": seconds, "ts": datetime.now(timezone.utc).isoformat()},
        )
    except Exception:
        logger.exception("door_open_failed seconds=%s", seconds)


@app.post("/door/open", status_code=status.HTTP_204_NO_CONTENT)
async def open_door(req: DoorOpenRequest):
    """Start a background task to unlock the door relay."""
    seconds = req.seconds or settings.door_unlock_seconds
    asyncio.create_task(_open_door_task(seconds))
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
