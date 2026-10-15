"""Periodic resume of stale access attempts."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..db import SessionLocal
from ..settings import settings
from .orchestrator import AccessOrchestrator
from .repository import AccessAttemptRepository
from .statuses import STALE_RESUME_STATUSES

logger = logging.getLogger(__name__)


class AccessAttemptReconciler:
    """Resumes CHARGED / DOOR_OPENING / REFUND_PENDING attempts after crashes."""

    def __init__(self, orchestrator: AccessOrchestrator) -> None:
        self._orch = orchestrator
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("access_attempt_reconciler_started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        # Run once shortly after startup, then periodically.
        await asyncio.sleep(2)
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("access_attempt_reconciler_tick_failed")
            await asyncio.sleep(max(5, settings.stale_attempt_seconds // 2))

    async def tick(self) -> None:
        older_than = datetime.now(timezone.utc) - timedelta(seconds=settings.stale_attempt_seconds)
        async with SessionLocal() as db:
            repo = AccessAttemptRepository(db)
            stale = await repo.list_stale(set(STALE_RESUME_STATUSES), older_than)
            for attempt in stale:
                logger.info(
                    "reconciler_resume attempt_id=%s status=%s", attempt.id, attempt.status
                )
                try:
                    await self._orch.resume_stale(db, attempt)
                except Exception:
                    logger.exception("reconciler_resume_failed attempt_id=%s", attempt.id)
