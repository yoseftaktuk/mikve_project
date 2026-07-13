from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .clients import ChipClient, HardwareClient
from .models import AccessLog
from .schemas import AccessDecisionResponse
from .settings import settings

logger = logging.getLogger(__name__)

PublishFn = Callable[[dict[str, Any]], Awaitable[None]]


class CashSession:
    """Tracks partial cash payments and resets them after inactivity."""

    def __init__(self, timeout_seconds: int) -> None:
        self._accumulated_cents = 0
        self._lock = asyncio.Lock()
        self._timeout_seconds = max(0, timeout_seconds)
        self._reset_task: asyncio.Task[None] | None = None
        self._publish: PublishFn | None = None

    @property
    def accumulated_cents(self) -> int:
        return self._accumulated_cents

    def set_publish(self, publish: PublishFn) -> None:
        """Attach the callback used to emit cash.reset events."""
        self._publish = publish

    async def shutdown(self) -> None:
        """Cancel any pending cash-session reset timer."""
        await self._cancel_reset_timer()

    async def _cancel_reset_timer(self) -> None:
        """Cancel the inactivity timer if one is running."""
        task = self._reset_task
        self._reset_task = None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _schedule_reset(self) -> None:
        """Start a timeout that clears unpaid cash if no more coins arrive."""
        if self._timeout_seconds <= 0 or self._accumulated_cents <= 0:
            return
        self._reset_task = asyncio.create_task(self._reset_after_timeout())

    async def _reset_after_timeout(self) -> None:
        """Wait for the inactivity timeout then reset the session."""
        try:
            await asyncio.sleep(self._timeout_seconds)
            await self.reset_expired()
        except asyncio.CancelledError:
            return

    async def reset_expired(self) -> None:
        """Clear accumulated cash and publish a cash.reset event."""
        async with self._lock:
            if self._accumulated_cents <= 0:
                return
            previous = self._accumulated_cents
            self._accumulated_cents = 0

        logger.info(
            "cash_session_reset previous_total_cents=%s timeout_seconds=%s",
            previous,
            self._timeout_seconds,
        )
        if self._publish is not None:
            await self._publish(
                {
                    "type": "cash.reset",
                    "reason": "timeout",
                    "previous_total_cents": previous,
                    "timeout_seconds": self._timeout_seconds,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )

    async def add(self, amount_cents: int) -> int:
        """Add inserted cash and refresh the inactivity reset timer."""
        await self._cancel_reset_timer()
        async with self._lock:
            self._accumulated_cents += amount_cents
            total = self._accumulated_cents
        if total > 0:
            self._schedule_reset()
        return total

    async def take_fee(self, fee_cents: int) -> int:
        """Deduct the entrance fee from accumulated cash and return the paid total."""
        await self._cancel_reset_timer()
        async with self._lock:
            paid = self._accumulated_cents
            self._accumulated_cents = max(0, self._accumulated_cents - fee_cents)
            return paid


async def process_chip_access(
    uid: str,
    db: AsyncSession,
    *,
    chip_client: ChipClient,
    hardware_client: HardwareClient,
    publish,
) -> AccessDecisionResponse:
    """Validate a chip, charge the entrance fee, and open the door if allowed."""
    fee = settings.entrance_fee_cents
    door_seconds = settings.door_unlock_seconds
    ts = datetime.now(timezone.utc).isoformat()

    try:
        chip = await chip_client.validate(uid)
    except ValueError:
        log = AccessLog(chip_id=None, uid=uid, decision="denied", reason="unknown_chip", fee_cents=fee)
        db.add(log)
        await db.commit()
        await publish({"type": "access.denied", "uid": uid, "reason": "unknown_chip", "ts": ts})
        return AccessDecisionResponse(granted=False, reason="unknown_chip", chip_id=None, fee_cents=fee)

    if not chip.is_enabled:
        log = AccessLog(
            chip_id=chip.chip_id,
            uid=chip.uid,
            decision="denied",
            reason="chip_disabled",
            fee_cents=fee,
            balance_before_cents=chip.balance_cents,
            balance_after_cents=chip.balance_cents,
        )
        db.add(log)
        await db.commit()
        await publish(
            {
                "type": "access.denied",
                "uid": chip.uid,
                "chip_id": chip.chip_id,
                "reason": "chip_disabled",
                "balance_cents": chip.balance_cents,
                "ts": ts,
            }
        )
        return AccessDecisionResponse(
            granted=False,
            reason="chip_disabled",
            chip_id=chip.chip_id,
            fee_cents=fee,
            balance_before_cents=chip.balance_cents,
            balance_after_cents=chip.balance_cents,
        )

    if chip.balance_cents < fee:
        log = AccessLog(
            chip_id=chip.chip_id,
            uid=chip.uid,
            decision="denied",
            reason="insufficient_balance",
            fee_cents=fee,
            balance_before_cents=chip.balance_cents,
            balance_after_cents=chip.balance_cents,
        )
        db.add(log)
        await db.commit()
        await publish(
            {
                "type": "access.denied",
                "uid": chip.uid,
                "chip_id": chip.chip_id,
                "reason": "insufficient_balance",
                "balance_cents": chip.balance_cents,
                "fee_cents": fee,
                "ts": ts,
            }
        )
        return AccessDecisionResponse(
            granted=False,
            reason="insufficient_balance",
            chip_id=chip.chip_id,
            fee_cents=fee,
            balance_before_cents=chip.balance_cents,
            balance_after_cents=chip.balance_cents,
        )

    before = chip.balance_cents
    try:
        after = await chip_client.adjust_balance(
            chip_id=chip.chip_id,
            delta_cents=-fee,
            reason="entry_fee",
            description="entrance fee charged",
        )
    except ValueError:
        log = AccessLog(
            chip_id=chip.chip_id,
            uid=chip.uid,
            decision="denied",
            reason="insufficient_balance",
            fee_cents=fee,
            balance_before_cents=before,
            balance_after_cents=before,
        )
        db.add(log)
        await db.commit()
        await publish(
            {
                "type": "access.denied",
                "uid": chip.uid,
                "chip_id": chip.chip_id,
                "reason": "insufficient_balance",
                "balance_cents": before,
                "fee_cents": fee,
                "ts": ts,
            }
        )
        return AccessDecisionResponse(
            granted=False,
            reason="insufficient_balance",
            chip_id=chip.chip_id,
            fee_cents=fee,
            balance_before_cents=before,
            balance_after_cents=before,
        )

    await hardware_client.open_door(seconds=door_seconds)
    log = AccessLog(
        chip_id=chip.chip_id,
        uid=chip.uid,
        decision="granted",
        reason="ok",
        fee_cents=fee,
        balance_before_cents=before,
        balance_after_cents=after,
    )
    db.add(log)
    await db.commit()
    await publish(
        {
            "type": "access.granted",
            "uid": chip.uid,
            "chip_id": chip.chip_id,
            "method": "chip",
            "fee_cents": fee,
            "balance_after_cents": after,
            "ts": ts,
        }
    )
    return AccessDecisionResponse(
        granted=True,
        reason="ok",
        chip_id=chip.chip_id,
        fee_cents=fee,
        balance_before_cents=before,
        balance_after_cents=after,
    )


async def process_cash_inserted(
    amount_cents: int,
    db: AsyncSession,
    *,
    cash_session: CashSession,
    hardware_client: HardwareClient,
    publish,
) -> tuple[bool, int]:
    """Accumulate cash and open the door once the entrance fee is reached."""
    fee = settings.entrance_fee_cents
    door_seconds = settings.door_unlock_seconds
    ts = datetime.now(timezone.utc).isoformat()

    total = await cash_session.add(amount_cents)
    await publish(
        {
            "type": "cash.accumulated",
            "amount_cents": amount_cents,
            "total_cents": total,
            "required_cents": fee,
            "ts": ts,
        }
    )

    if total < fee:
        logger.info("cash_partial total_cents=%s required_cents=%s", total, fee)
        return False, total

    paid_total = await cash_session.take_fee(fee)
    remaining = paid_total - fee
    await hardware_client.open_door(seconds=door_seconds)
    log = AccessLog(
        chip_id=None,
        uid=None,
        decision="granted",
        reason="cash_paid",
        fee_cents=fee,
        balance_before_cents=paid_total,
        balance_after_cents=paid_total - fee,
    )
    db.add(log)
    await db.commit()
    await publish(
        {
            "type": "access.granted",
            "method": "cash",
            "reason": "cash_paid",
            "fee_cents": fee,
            "paid_total_cents": paid_total,
            "remaining_cents": remaining,
            "ts": ts,
        }
    )
    logger.info("cash_access_granted paid_total_cents=%s fee_cents=%s", paid_total, fee)
    return True, remaining
