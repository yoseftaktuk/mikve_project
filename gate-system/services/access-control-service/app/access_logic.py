from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .clients import ChipClient, HardwareClient
from .models import AccessLog
from .schemas import AccessDecisionResponse
from .settings import settings

logger = logging.getLogger(__name__)

PublishFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class CashTakeResult:
    """Result of an atomic cash fee take."""

    ok: bool
    paid_total_cents: int = 0


class CashSession:
    """Tracks partial cash payments and resets them after inactivity."""

    def __init__(self, timeout_seconds: int) -> None:
        self._accumulated_cents = 0
        self._lock = asyncio.Lock()
        self._timeout_seconds = max(0, timeout_seconds)
        self._reset_task: asyncio.Task[None] | None = None
        self._publish: PublishFn | None = None
        self._taken_attempt_id: str | None = None
        self._last_paid_total: int = 0
        self._last_fee_cents: int = 0

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

    async def clear_for_other_method(self) -> int:
        """Clear abandoned partial cash when chip/fingerprint entry succeeds.

        Returns the cleared amount in cents (0 if the session was already empty).
        """
        await self._cancel_reset_timer()
        async with self._lock:
            previous = self._accumulated_cents
            if previous <= 0:
                return 0
            self._accumulated_cents = 0

        logger.info(
            "cash_session_cleared reason=method_switched previous_total_cents=%s",
            previous,
        )
        if self._publish is not None:
            await self._publish(
                {
                    "type": "cash.reset",
                    "reason": "method_switched",
                    "previous_total_cents": previous,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        return previous

    async def add(self, amount_cents: int) -> int:
        """Add inserted cash and refresh the inactivity reset timer."""
        await self._cancel_reset_timer()
        async with self._lock:
            self._accumulated_cents += amount_cents
            total = self._accumulated_cents
        if total > 0:
            self._schedule_reset()
        return total

    async def take_fee(self, fee_cents: int, *, attempt_id: str | None = None) -> int:
        """Take the entrance fee from accumulated cash and return the paid total.

        Overpayment is discarded (session balance becomes 0). When attempt_id is set,
        a second call for the same attempt is idempotent.
        Prefer CashTakeResult via try_pay for new saga callers.
        """
        result = await self.try_pay(fee_cents=fee_cents, attempt_id=attempt_id or "")
        if not result.ok:
            return 0
        return result.paid_total_cents

    async def try_pay(self, *, fee_cents: int, attempt_id: str) -> "CashTakeResult":
        """Atomically take the fee for an attempt (idempotent per attempt_id).

        On success the session balance is cleared to 0 so overpayment never carries
        into the next visitor's payment. Discarded overage emits cash.reset.
        """
        await self._cancel_reset_timer()
        discarded_cents = 0
        paid = 0
        async with self._lock:
            if attempt_id and self._taken_attempt_id == attempt_id:
                return CashTakeResult(ok=True, paid_total_cents=self._last_paid_total)
            if self._accumulated_cents < fee_cents:
                return CashTakeResult(ok=False, paid_total_cents=self._accumulated_cents)
            paid = self._accumulated_cents
            discarded_cents = max(0, paid - fee_cents)
            # Discard leftover so change does not seed the next payment.
            self._accumulated_cents = 0
            if attempt_id:
                self._taken_attempt_id = attempt_id
                self._last_paid_total = paid
                self._last_fee_cents = fee_cents

        if discarded_cents > 0:
            logger.info(
                "cash_overpay_discarded paid_total_cents=%s fee_cents=%s discarded_cents=%s",
                paid,
                fee_cents,
                discarded_cents,
            )
            if self._publish is not None:
                await self._publish(
                    {
                        "type": "cash.reset",
                        "reason": "overpay_discarded",
                        "previous_total_cents": paid,
                        "discarded_cents": discarded_cents,
                        "fee_cents": fee_cents,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )
        return CashTakeResult(ok=True, paid_total_cents=paid)

    async def restore_fee(self, attempt_id: str) -> bool:
        """Best-effort put the fee back into the session for the same attempt."""
        async with self._lock:
            if self._taken_attempt_id != attempt_id:
                return False
            self._accumulated_cents += self._last_fee_cents
            self._taken_attempt_id = None
            self._last_fee_cents = 0
            if self._accumulated_cents > 0:
                self._schedule_reset()
            return True


async def process_chip_access(
    uid: str,
    db: AsyncSession,
    *,
    chip_client: ChipClient,
    hardware_client: HardwareClient,
    publish,
    cash_session: CashSession | None = None,
    hardware_event_id: str | None = None,
) -> AccessDecisionResponse:
    """Validate a chip, charge the entrance fee, and open the door if allowed."""
    if settings.access_saga_enabled:
        from .saga import AccessOrchestrator

        session = cash_session or CashSession(timeout_seconds=settings.cash_session_timeout_seconds)
        orch = AccessOrchestrator(
            chip_client=chip_client,
            hardware_client=hardware_client,
            cash_session=session,
            publish=publish,
        )
        return await orch.run_chip_access(uid, db, hardware_event_id=hardware_event_id)

    return await _legacy_process_chip_access(
        uid, db, chip_client=chip_client, hardware_client=hardware_client, publish=publish
    )


async def _legacy_process_chip_access(
    uid: str,
    db: AsyncSession,
    *,
    chip_client: ChipClient,
    hardware_client: HardwareClient,
    publish,
) -> AccessDecisionResponse:
    """Pre-saga chip access path (kept behind ACCESS_SAGA_ENABLED=false)."""
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
    chip_client: ChipClient | None = None,
    hardware_event_id: str | None = None,
) -> tuple[bool, int]:
    """Accumulate cash and open the door once the entrance fee is reached."""
    if settings.access_saga_enabled:
        from .saga import AccessOrchestrator

        orch = AccessOrchestrator(
            chip_client=chip_client or ChipClient(),
            hardware_client=hardware_client,
            cash_session=cash_session,
            publish=publish,
        )
        return await orch.run_cash_inserted(
            amount_cents, db, hardware_event_id=hardware_event_id
        )

    return await _legacy_process_cash_inserted(
        amount_cents,
        db,
        cash_session=cash_session,
        hardware_client=hardware_client,
        publish=publish,
    )


async def _legacy_process_cash_inserted(
    amount_cents: int,
    db: AsyncSession,
    *,
    cash_session: CashSession,
    hardware_client: HardwareClient,
    publish,
) -> tuple[bool, int]:
    """Pre-saga cash path (kept behind ACCESS_SAGA_ENABLED=false)."""
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
