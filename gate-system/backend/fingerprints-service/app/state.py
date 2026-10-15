"""Shared runtime objects for fingerprints startup and routes."""

from __future__ import annotations

import json

import redis.asyncio as redis

redis_client: redis.Redis | None = None


async def publish(event: dict) -> None:
    """Publish a member event to Redis pub/sub."""
    if redis_client is None:
        return
    await redis_client.publish("member.events", json.dumps(event))
