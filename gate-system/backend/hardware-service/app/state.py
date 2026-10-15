"""Shared runtime objects for hardware startup and routes."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis

from .fingerprint import STEP_STORED
from .hardware import HardwareAdapter

logger = logging.getLogger(__name__)

redis_client: redis.Redis | None = None
adapter: HardwareAdapter | None = None


def require_adapter() -> HardwareAdapter:
    """Return the running adapter, or fail if startup has not finished."""
    if adapter is None:
        raise RuntimeError("hardware adapter is not running")
    return adapter


async def publish(channel: str, event: dict) -> None:
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
    await publish(
        "hardware.events",
        {
            "type": "rfid.scan",
            "uid": uid,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def on_cash_inserted(amount_cents: int) -> None:
    """Publish a cash.inserted event when a coin is accepted."""
    await publish(
        "hardware.events",
        {
            "type": "cash.inserted",
            "amount_cents": amount_cents,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def on_fingerprint_scan(slot: int, confidence: int) -> None:
    """Publish a fingerprint.scan event when a stored template matches."""
    await publish(
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
    await publish(
        "hardware.events",
        {"type": "fingerprint.unmatched", "ts": datetime.now(timezone.utc).isoformat()},
    )


async def on_fingerprint_progress(session_id: str, step: str, slot: int | None) -> None:
    """Publish an enrollment progress step so the dashboard can guide the user."""
    await publish(
        "hardware.events",
        {
            "type": "fingerprint.enroll_progress",
            "session_id": session_id,
            "step": step,
            "slot": slot,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def open_door_task(seconds: int, *, operation_id: str | None = None, attempt_id: str | None = None) -> None:
    """Unlock the door for the given seconds and publish door.opened."""
    try:
        await require_adapter().open_door(seconds=seconds)
        payload = {
            "type": "door.opened",
            "seconds": seconds,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if operation_id:
            payload["operation_id"] = operation_id
        if attempt_id:
            payload["attempt_id"] = attempt_id
        await publish("hardware.events", payload)
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
        await publish("hardware.events", fail)


async def enroll_task(session_id: str) -> None:
    """Run enrollment and publish fingerprint.enrolled once a template is stored."""
    try:
        result = await require_adapter().enroll_fingerprint(session_id)
    except Exception:
        logger.exception("fingerprint_enroll_failed session=%s", session_id)
        await on_fingerprint_progress(session_id, "failed", None)
        return

    if result.get("step") != STEP_STORED:
        return

    await publish(
        "hardware.events",
        {
            "type": "fingerprint.enrolled",
            "session_id": session_id,
            "slot": result.get("slot"),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )
