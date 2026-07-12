from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import redis.asyncio as redis
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class PubSubFanout:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._websockets: set[WebSocket] = set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._redis = redis.from_url(self._redis_url, decode_responses=True)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for ws in list(self._websockets):
            await ws.close()
        if self._redis:
            await self._redis.aclose()

    async def register(self, ws: WebSocket) -> None:
        self._websockets.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._websockets.discard(ws)

    async def publish_local(self, event: dict) -> None:
        await self._broadcast(json.dumps(event))

    async def _run(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("hardware.events", "access.events", "chip.events")
        async for msg in self._iter_messages(pubsub):
            await self._broadcast(msg)

    async def _broadcast(self, raw: str) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._websockets):
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._websockets.discard(ws)

    async def _iter_messages(self, pubsub) -> AsyncIterator[str]:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and isinstance(msg.get("data"), str):
                yield msg["data"]
            await asyncio.sleep(0.05)

