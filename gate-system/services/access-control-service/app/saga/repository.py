"""CAS repository for access attempts and related rows."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AccessAttempt,
    AttemptAuditLog,
    CashReceipt,
    DoorOperation,
    PaymentTransaction,
    RefundTransaction,
)
from .statuses import VALID_TRANSITIONS

logger = logging.getLogger(__name__)


class TransitionConflictError(Exception):
    """Raised when CAS transition fails because status changed."""


class AccessAttemptRepository:
    """Persist attempts and enforce valid state transitions."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._last_transition_at: dict[uuid.UUID, datetime] = {}

    async def create(self, attempt: AccessAttempt) -> AccessAttempt:
        self._db.add(attempt)
        await self._db.flush()
        await self._append_audit(
            attempt_id=attempt.id,
            correlation_id=attempt.correlation_id,
            subject_ref=attempt.subject_ref,
            old_status=None,
            new_status=attempt.status,
            reason="created",
            duration_ms=None,
        )
        self._last_transition_at[attempt.id] = datetime.now(timezone.utc)
        return attempt

    async def get(self, attempt_id: uuid.UUID) -> AccessAttempt | None:
        return await self._db.get(AccessAttempt, attempt_id)

    async def get_by_hardware_event_id(self, hardware_event_id: str) -> AccessAttempt | None:
        return await self._db.scalar(
            select(AccessAttempt).where(AccessAttempt.hardware_event_id == hardware_event_id)
        )

    async def transition(
        self,
        attempt_id: uuid.UUID,
        expected_from: str,
        to: str,
        reason: str,
        *,
        failure_reason: str | None = None,
        charge_taken: bool | None = None,
        door_attempt_count: int | None = None,
        balance_after_cents: int | None = None,
        receipt_id: uuid.UUID | None = None,
    ) -> AccessAttempt:
        if (expected_from, to) not in VALID_TRANSITIONS:
            raise ValueError(f"invalid_transition:{expected_from}->{to}")

        now = datetime.now(timezone.utc)
        values: dict = {
            "status": to,
            "updated_at": now,
        }
        if failure_reason is not None:
            values["failure_reason"] = failure_reason
        if charge_taken is not None:
            values["charge_taken"] = charge_taken
        if door_attempt_count is not None:
            values["door_attempt_count"] = door_attempt_count
        if balance_after_cents is not None:
            values["balance_after_cents"] = balance_after_cents
        if receipt_id is not None:
            values["receipt_id"] = receipt_id

        result = await self._db.execute(
            update(AccessAttempt)
            .where(AccessAttempt.id == attempt_id, AccessAttempt.status == expected_from)
            .values(**values)
        )
        if result.rowcount != 1:
            raise TransitionConflictError(
                f"CAS failed attempt={attempt_id} expected={expected_from} to={to}"
            )

        attempt = await self.get(attempt_id)
        assert attempt is not None

        prev = self._last_transition_at.get(attempt_id)
        duration_ms = int((now - prev).total_seconds() * 1000) if prev else None
        await self._append_audit(
            attempt_id=attempt.id,
            correlation_id=attempt.correlation_id,
            subject_ref=attempt.subject_ref,
            old_status=expected_from,
            new_status=to,
            reason=reason,
            duration_ms=duration_ms,
        )
        self._last_transition_at[attempt_id] = now
        logger.info(
            "attempt_transition attempt_id=%s %s->%s reason=%s duration_ms=%s",
            attempt_id,
            expected_from,
            to,
            reason,
            duration_ms,
        )
        return attempt

    async def list_stale(self, states: set[str], older_than: datetime) -> list[AccessAttempt]:
        rows = await self._db.scalars(
            select(AccessAttempt).where(
                AccessAttempt.status.in_(states),
                AccessAttempt.updated_at < older_than,
            )
        )
        return list(rows.all())

    async def add_payment(self, row: PaymentTransaction) -> None:
        self._db.add(row)

    async def get_payment_by_key(self, key: str) -> PaymentTransaction | None:
        return await self._db.scalar(
            select(PaymentTransaction).where(PaymentTransaction.idempotency_key == key)
        )

    async def add_refund(self, row: RefundTransaction) -> None:
        self._db.add(row)

    async def get_refund_by_key(self, key: str) -> RefundTransaction | None:
        return await self._db.scalar(
            select(RefundTransaction).where(RefundTransaction.idempotency_key == key)
        )

    async def add_door_operation(self, row: DoorOperation) -> None:
        self._db.add(row)
        await self._db.flush()

    async def finish_door_operation(
        self, operation_id: uuid.UUID, status: str, error_code: str | None = None
    ) -> None:
        await self._db.execute(
            update(DoorOperation)
            .where(DoorOperation.id == operation_id)
            .values(
                status=status,
                error_code=error_code,
                finished_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def get_receipt_for_attempt(self, attempt_id: uuid.UUID) -> CashReceipt | None:
        return await self._db.scalar(select(CashReceipt).where(CashReceipt.attempt_id == attempt_id))

    async def add_receipt(self, row: CashReceipt) -> CashReceipt:
        self._db.add(row)
        await self._db.flush()
        return row

    async def get_receipt_by_code(self, redeem_code: str) -> CashReceipt | None:
        return await self._db.scalar(select(CashReceipt).where(CashReceipt.redeem_code == redeem_code))

    async def _append_audit(
        self,
        *,
        attempt_id: uuid.UUID,
        correlation_id: uuid.UUID,
        subject_ref: str,
        old_status: str | None,
        new_status: str,
        reason: str,
        duration_ms: int | None,
    ) -> None:
        self._db.add(
            AttemptAuditLog(
                attempt_id=attempt_id,
                correlation_id=correlation_id,
                subject_ref=subject_ref,
                old_status=old_status,
                new_status=new_status,
                reason=reason,
                duration_ms=duration_ms,
            )
        )
