"""Access-attempt saga orchestrator: charge → confirmed door → compensate on failure."""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..access_logic import CashSession
from ..clients import (
    ChipClient,
    DoorRejectedError,
    HardwareClient,
    HardwareUnavailableError,
)
from ..models import AccessLog
from ..schemas import AccessDecisionResponse
from ..settings import settings
from .models import AccessAttempt, CashReceipt, DoorOperation, PaymentTransaction, RefundTransaction
from .repository import AccessAttemptRepository, TransitionConflictError
from .statuses import (
    METHOD_CASH,
    METHOD_CHIP,
    METHOD_FINGERPRINT,
    STATUS_CHARGED,
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_DOOR_OPENING,
    STATUS_FAILED,
    STATUS_MANUAL_REVIEW,
    STATUS_REFUND_PENDING,
    STATUS_REFUNDED,
    STATUS_VALIDATED,
    charge_key,
    refund_key,
)

logger = logging.getLogger(__name__)

PublishFn = Callable[[dict], Awaitable[None]]

DOOR_CONFIRMED = "confirmed"
DOOR_TIMEOUT = "timeout"
DOOR_UNAVAILABLE = "hardware_unavailable"
DOOR_REJECTED = "rejected"


class AccessOrchestrator:
    """Runs one access attempt through the saga state machine."""

    def __init__(
        self,
        *,
        chip_client: ChipClient,
        hardware_client: HardwareClient,
        cash_session: CashSession,
        publish: PublishFn,
    ) -> None:
        self._chip = chip_client
        self._hardware = hardware_client
        self._cash = cash_session
        self._publish = publish

    async def run_chip_access(
        self,
        uid: str,
        db: AsyncSession,
        *,
        hardware_event_id: str | None = None,
    ) -> AccessDecisionResponse:
        """Validate, charge, open door for an RFID/NFC chip."""
        repo = AccessAttemptRepository(db)
        if hardware_event_id:
            existing = await repo.get_by_hardware_event_id(hardware_event_id)
            if existing is not None:
                return self._decision_from_attempt(existing)

        attempt_id = uuid.uuid4()
        correlation_id = attempt_id
        fee = settings.entrance_fee_cents
        attempt = AccessAttempt(
            id=attempt_id,
            correlation_id=correlation_id,
            method=METHOD_CHIP,
            subject_type="uid",
            subject_ref=uid,
            uid=uid,
            status=STATUS_CREATED,
            fee_cents=fee,
            hardware_event_id=hardware_event_id,
            door_attempt_count=0,
            charge_taken=False,
        )
        await repo.create(attempt)
        await db.commit()

        try:
            chip = await self._chip.validate(uid)
        except ValueError:
            await self._fail_never_charged(repo, attempt, "unknown_chip")
            await self._legacy_deny(db, uid=uid, chip_id=None, reason="unknown_chip", fee=fee)
            await db.commit()
            await self._publish_denied(uid=uid, chip_id=None, reason="unknown_chip", fee=fee)
            return AccessDecisionResponse(granted=False, reason="unknown_chip", chip_id=None, fee_cents=fee)

        attempt.chip_id = uuid.UUID(chip.chip_id)
        attempt.holder_name = chip.holder_name
        attempt.balance_before_cents = chip.balance_cents
        await db.commit()

        if not chip.is_enabled:
            await self._fail_never_charged(repo, attempt, "chip_disabled")
            await self._legacy_deny(
                db,
                uid=chip.uid,
                chip_id=chip.chip_id,
                reason="chip_disabled",
                fee=fee,
                before=chip.balance_cents,
            )
            await db.commit()
            await self._publish_denied(
                uid=chip.uid, chip_id=chip.chip_id, reason="chip_disabled", fee=fee, balance=chip.balance_cents
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
            await self._fail_never_charged(repo, attempt, "insufficient_balance")
            await self._legacy_deny(
                db,
                uid=chip.uid,
                chip_id=chip.chip_id,
                reason="insufficient_balance",
                fee=fee,
                before=chip.balance_cents,
            )
            await db.commit()
            await self._publish_denied(
                uid=chip.uid,
                chip_id=chip.chip_id,
                reason="insufficient_balance",
                fee=fee,
                balance=chip.balance_cents,
            )
            return AccessDecisionResponse(
                granted=False,
                reason="insufficient_balance",
                chip_id=chip.chip_id,
                fee_cents=fee,
                balance_before_cents=chip.balance_cents,
                balance_after_cents=chip.balance_cents,
            )

        await repo.transition(attempt.id, STATUS_CREATED, STATUS_VALIDATED, "chip_ok")
        await db.commit()
        return await self._charge_and_open_balance(
            repo,
            db,
            attempt,
            chip_id=chip.chip_id,
            uid=chip.uid,
            method=METHOD_CHIP,
            before=chip.balance_cents,
            holder_name=chip.holder_name,
        )

    async def run_fingerprint_approve(
        self,
        *,
        approval_id: str,
        uid: str,
        chip_id: str,
        holder_name: str | None,
        balance_cents: int,
        fee_cents: int,
        db: AsyncSession,
    ) -> AccessDecisionResponse:
        """Start saga after staff confirms a fingerprint (approval already consumed)."""
        repo = AccessAttemptRepository(db)
        attempt_id = uuid.uuid4()
        attempt = AccessAttempt(
            id=attempt_id,
            correlation_id=attempt_id,
            method=METHOD_FINGERPRINT,
            subject_type="uid",
            subject_ref=uid,
            uid=uid,
            chip_id=uuid.UUID(chip_id),
            status=STATUS_CREATED,
            fee_cents=fee_cents,
            balance_before_cents=balance_cents,
            holder_name=holder_name,
            hardware_event_id=f"fp-approve:{approval_id}",
            door_attempt_count=0,
            charge_taken=False,
        )
        await repo.create(attempt)
        await repo.transition(attempt.id, STATUS_CREATED, STATUS_VALIDATED, "fp_approved")
        await db.commit()
        return await self._charge_and_open_balance(
            repo,
            db,
            attempt,
            chip_id=chip_id,
            uid=uid,
            method=METHOD_FINGERPRINT,
            before=balance_cents,
            holder_name=holder_name,
        )

    async def run_cash_inserted(
        self,
        amount_cents: int,
        db: AsyncSession,
        *,
        hardware_event_id: str | None = None,
    ) -> tuple[bool, int]:
        """Accumulate cash; when fee reached, run saga with atomic take_fee."""
        if amount_cents <= 0:
            return False, self._cash.accumulated_cents

        fee = settings.entrance_fee_cents
        total = await self._cash.add(amount_cents)
        await self._emit(
            {
                "type": "cash.accumulated",
                "amount_cents": amount_cents,
                "total_cents": total,
                "required_cents": fee,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        if total < fee:
            logger.info("cash_partial total_cents=%s required_cents=%s", total, fee)
            return False, total

        repo = AccessAttemptRepository(db)
        if hardware_event_id:
            existing = await repo.get_by_hardware_event_id(hardware_event_id)
            if existing is not None:
                return existing.status == STATUS_COMPLETED, self._cash.accumulated_cents

        attempt_id = uuid.uuid4()
        attempt = AccessAttempt(
            id=attempt_id,
            correlation_id=attempt_id,
            method=METHOD_CASH,
            subject_type="cash",
            subject_ref="cash",
            status=STATUS_CREATED,
            fee_cents=fee,
            hardware_event_id=hardware_event_id,
            door_attempt_count=0,
            charge_taken=False,
        )
        await repo.create(attempt)
        await repo.transition(attempt.id, STATUS_CREATED, STATUS_VALIDATED, "cash_enough")
        await db.commit()

        take = await self._cash.try_pay(fee_cents=fee, attempt_id=str(attempt.id))
        if not take.ok:
            await self._fail_never_charged(repo, attempt, "cash_take_failed")
            await db.commit()
            return False, self._cash.accumulated_cents

        key = charge_key(str(attempt.id))
        await repo.add_payment(
            PaymentTransaction(
                attempt_id=attempt.id,
                amount_cents=fee,
                status="succeeded",
                provider="cash",
                idempotency_key=key,
                correlation_id=attempt.correlation_id,
            )
        )
        await repo.transition(
            attempt.id, STATUS_VALIDATED, STATUS_CHARGED, "cash_take_ok", charge_taken=True
        )
        await db.commit()

        door_ok = await self._open_door_with_retries(repo, db, attempt)
        if door_ok:
            await repo.transition(attempt.id, STATUS_DOOR_OPENING, STATUS_COMPLETED, "door_confirmed")
            remaining = take.paid_total_cents - fee
            await self._legacy_grant_cash(db, fee=fee, paid_total=take.paid_total_cents)
            await db.commit()
            await self._emit(
                {
                    "type": "access.granted",
                    "method": "cash",
                    "reason": "cash_paid",
                    "fee_cents": fee,
                    "paid_total_cents": take.paid_total_cents,
                    "remaining_cents": remaining,
                    "attempt_id": str(attempt.id),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return True, remaining

        await self._compensate_cash(repo, db, attempt)
        return False, self._cash.accumulated_cents

    async def resume_stale(self, db: AsyncSession, attempt: AccessAttempt) -> None:
        """Reconciler entry: continue door or compensation for a stale attempt."""
        repo = AccessAttemptRepository(db)
        current = await repo.get(attempt.id)
        if current is None:
            return
        if current.status == STATUS_CHARGED:
            await repo.transition(current.id, STATUS_CHARGED, STATUS_DOOR_OPENING, "reconciler_door_start")
            await db.commit()
            current = await repo.get(current.id)
            assert current is not None
            ok = await self._open_door_with_retries(repo, db, current)
            if ok:
                await repo.transition(current.id, STATUS_DOOR_OPENING, STATUS_COMPLETED, "door_confirmed")
                await db.commit()
                if current.method != METHOD_CASH:
                    await self._cash.clear_for_other_method()
                return
            await self._begin_compensation(repo, db, current)
            return
        if current.status == STATUS_DOOR_OPENING:
            ok = await self._open_door_with_retries(repo, db, current)
            if ok:
                await repo.transition(current.id, STATUS_DOOR_OPENING, STATUS_COMPLETED, "door_confirmed")
                await db.commit()
                if current.method != METHOD_CASH:
                    await self._cash.clear_for_other_method()
                return
            await self._begin_compensation(repo, db, current)
            return
        if current.status == STATUS_REFUND_PENDING:
            await self._run_compensation(repo, db, current)

    async def redeem_receipt(self, redeem_code: str, staff_id: str, db: AsyncSession) -> CashReceipt:
        repo = AccessAttemptRepository(db)
        receipt = await repo.get_receipt_by_code(redeem_code.strip())
        if receipt is None:
            raise ValueError("receipt_not_found")
        if receipt.status != "issued":
            raise ValueError("receipt_not_redeemable")
        receipt.status = "redeemed"
        receipt.redeemed_at = datetime.now(timezone.utc)
        receipt.redeemed_by = staff_id
        await db.commit()
        return receipt

    async def resolve_manual_review(
        self, attempt_id: uuid.UUID, note: str, db: AsyncSession
    ) -> AccessAttempt:
        repo = AccessAttemptRepository(db)
        attempt = await repo.get(attempt_id)
        if attempt is None:
            raise ValueError("attempt_not_found")
        if attempt.status != STATUS_MANUAL_REVIEW:
            raise ValueError("not_in_manual_review")
        await repo.transition(
            attempt.id, STATUS_MANUAL_REVIEW, STATUS_REFUNDED, f"staff_marked_resolved:{note[:40]}"
        )
        await db.commit()
        return attempt

    async def _charge_and_open_balance(
        self,
        repo: AccessAttemptRepository,
        db: AsyncSession,
        attempt: AccessAttempt,
        *,
        chip_id: str,
        uid: str,
        method: str,
        before: int,
        holder_name: str | None,
    ) -> AccessDecisionResponse:
        fee = attempt.fee_cents
        key = charge_key(str(attempt.id))
        existing_pay = await repo.get_payment_by_key(key)
        if existing_pay is None:
            await repo.add_payment(
                PaymentTransaction(
                    attempt_id=attempt.id,
                    amount_cents=fee,
                    status="pending",
                    provider="chip",
                    idempotency_key=key,
                    correlation_id=attempt.correlation_id,
                )
            )
            await db.flush()

        try:
            after = await self._chip.adjust_balance(
                chip_id=chip_id,
                delta_cents=-fee,
                reason="entry_fee",
                description=f"entrance fee ({method})",
                idempotency_key=key,
            )
        except ValueError:
            await self._fail_never_charged(repo, attempt, "insufficient_balance")
            await self._legacy_deny(
                db, uid=uid, chip_id=chip_id, reason="insufficient_balance", fee=fee, before=before
            )
            await db.commit()
            await self._publish_denied(
                uid=uid, chip_id=chip_id, reason="insufficient_balance", fee=fee, balance=before
            )
            return AccessDecisionResponse(
                granted=False,
                reason="insufficient_balance",
                chip_id=chip_id,
                fee_cents=fee,
                balance_before_cents=before,
                balance_after_cents=before,
            )
        except Exception:
            logger.exception("ledger_charge_failed attempt_id=%s", attempt.id)
            await self._fail_never_charged(repo, attempt, "ledger_unavailable")
            await db.commit()
            return AccessDecisionResponse(
                granted=False,
                reason="ledger_unavailable",
                chip_id=chip_id,
                fee_cents=fee,
                balance_before_cents=before,
                balance_after_cents=before,
            )

        pay = await repo.get_payment_by_key(key)
        if pay is not None:
            pay.status = "succeeded"
        await repo.transition(
            attempt.id,
            STATUS_VALIDATED,
            STATUS_CHARGED,
            "ledger_charge_ok",
            charge_taken=True,
            balance_after_cents=after,
        )
        await db.commit()

        door_ok = await self._open_door_with_retries(repo, db, attempt)
        if door_ok:
            await repo.transition(attempt.id, STATUS_DOOR_OPENING, STATUS_COMPLETED, "door_confirmed")
            await self._legacy_grant_chip(
                db, uid=uid, chip_id=chip_id, fee=fee, before=before, after=after
            )
            await db.commit()
            await self._cash.clear_for_other_method()
            await self._emit(
                {
                    "type": "access.granted",
                    "uid": uid,
                    "chip_id": chip_id,
                    "method": method,
                    "holder_name": holder_name,
                    "fee_cents": fee,
                    "balance_after_cents": after,
                    "attempt_id": str(attempt.id),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return AccessDecisionResponse(
                granted=True,
                reason="ok",
                chip_id=chip_id,
                fee_cents=fee,
                balance_before_cents=before,
                balance_after_cents=after,
            )

        await self._begin_compensation(repo, db, attempt)
        refreshed = await repo.get(attempt.id)
        assert refreshed is not None
        # After refund, balance should be restored; report before for clarity if refund pending.
        return AccessDecisionResponse(
            granted=False,
            reason=refreshed.failure_reason or "door_failed",
            chip_id=chip_id,
            fee_cents=fee,
            balance_before_cents=before,
            balance_after_cents=refreshed.balance_after_cents or before,
        )

    async def _open_door_with_retries(
        self, repo: AccessAttemptRepository, db: AsyncSession, attempt: AccessAttempt
    ) -> bool:
        current = await repo.get(attempt.id)
        assert current is not None
        if current.status == STATUS_CHARGED:
            await repo.transition(current.id, STATUS_CHARGED, STATUS_DOOR_OPENING, "door_request_sent")
            await db.commit()
            current = await repo.get(current.id)
            assert current is not None

        max_tries = max(1, settings.door_max_retries)
        # HTTP confirm must outlive a blocking unlock hold if hardware awaits the full pulse.
        timeout_s = max(
            settings.door_open_timeout_ms / 1000.0,
            float(settings.door_unlock_seconds) + 2.0,
        )
        errors: list[str] = []

        while (current.door_attempt_count or 0) < max_tries:
            index = (current.door_attempt_count or 0) + 1
            op = DoorOperation(
                id=uuid.uuid4(),
                attempt_id=current.id,
                attempt_index=index,
                status="requested",
                correlation_id=current.correlation_id,
            )
            await repo.add_door_operation(op)
            await repo.transition(
                current.id,
                STATUS_DOOR_OPENING,
                STATUS_DOOR_OPENING,
                "door_retry" if index > 1 else "door_try",
                door_attempt_count=index,
            )
            await db.commit()

            result = await self._call_door(current, op.id, timeout_s)
            if result == DOOR_CONFIRMED:
                await repo.finish_door_operation(op.id, "confirmed")
                await db.commit()
                return True

            await repo.finish_door_operation(op.id, "timeout" if result == DOOR_TIMEOUT else "error", result)
            await db.commit()
            errors.append(result)
            await self._alert(
                "DoorTimeout" if result == DOOR_TIMEOUT else "HardwareUnavailable",
                {
                    "attempt_id": str(current.id),
                    "attempt_index": index,
                    "timeout_ms": settings.door_open_timeout_ms,
                    "error": result,
                },
            )
            if index < max_tries:
                await asyncio.sleep(settings.door_retry_delay_ms / 1000.0)
            current = await repo.get(current.id)
            assert current is not None

        current.failure_reason = "door_exhausted"
        await db.flush()
        logger.warning("door_exhausted attempt_id=%s errors=%s", current.id, errors)
        return False

    async def _call_door(
        self, attempt: AccessAttempt, operation_id: uuid.UUID, timeout_s: float
    ) -> str:
        try:
            data = await self._hardware.open_door(
                settings.door_unlock_seconds,
                operation_id=str(operation_id),
                attempt_id=str(attempt.id),
                correlation_id=str(attempt.correlation_id),
                timeout_seconds=timeout_s,
            )
            status = str(data.get("status", "")).lower()
            if status == DOOR_CONFIRMED:
                return DOOR_CONFIRMED
            return DOOR_REJECTED
        except HardwareUnavailableError:
            return DOOR_UNAVAILABLE
        except DoorRejectedError:
            return DOOR_REJECTED
        except Exception:
            logger.exception("door_call_failed attempt_id=%s", attempt.id)
            return DOOR_TIMEOUT

    async def _begin_compensation(
        self, repo: AccessAttemptRepository, db: AsyncSession, attempt: AccessAttempt
    ) -> None:
        current = await repo.get(attempt.id)
        assert current is not None
        if current.status == STATUS_DOOR_OPENING:
            await repo.transition(
                current.id,
                STATUS_DOOR_OPENING,
                STATUS_FAILED,
                "door_exhausted",
                failure_reason="door_exhausted",
            )
            await db.commit()
            current = await repo.get(current.id)
            assert current is not None
        if current.status == STATUS_FAILED and current.charge_taken:
            await repo.transition(current.id, STATUS_FAILED, STATUS_REFUND_PENDING, "needs_compensation")
            await db.commit()
            await self._alert(
                "DoorFailedAfterCharge",
                {
                    "attempt_id": str(current.id),
                    "method": current.method,
                    "fee_cents": current.fee_cents,
                },
            )
            await self._run_compensation(repo, db, current)

    async def _run_compensation(
        self, repo: AccessAttemptRepository, db: AsyncSession, attempt: AccessAttempt
    ) -> None:
        current = await repo.get(attempt.id)
        assert current is not None
        if current.method == METHOD_CASH:
            await self._compensate_cash(repo, db, current)
            return
        await self._compensate_balance(repo, db, current)

    async def _compensate_balance(
        self, repo: AccessAttemptRepository, db: AsyncSession, attempt: AccessAttempt
    ) -> None:
        if attempt.chip_id is None:
            await repo.transition(
                attempt.id, STATUS_REFUND_PENDING, STATUS_MANUAL_REVIEW, "refund_failed_no_chip"
            )
            await db.commit()
            await self._alert("RefundFailed", {"attempt_id": str(attempt.id), "error": "no_chip"})
            return

        key = refund_key(str(attempt.id))
        existing = await repo.get_refund_by_key(key)
        if existing is None:
            await repo.add_refund(
                RefundTransaction(
                    attempt_id=attempt.id,
                    amount_cents=attempt.fee_cents,
                    status="pending",
                    provider="chip",
                    idempotency_key=key,
                    correlation_id=attempt.correlation_id,
                )
            )
            await db.flush()

        last_error = "refund_failed"
        for _ in range(max(1, settings.refund_max_retries)):
            try:
                after = await self._chip.adjust_balance(
                    chip_id=str(attempt.chip_id),
                    delta_cents=attempt.fee_cents,
                    reason="entry_fee_refund",
                    description=f"access-refund:{attempt.id}",
                    idempotency_key=key,
                )
                refund = await repo.get_refund_by_key(key)
                if refund is not None:
                    refund.status = "succeeded"
                await repo.transition(
                    attempt.id,
                    STATUS_REFUND_PENDING,
                    STATUS_REFUNDED,
                    "balance_refund_ok",
                    balance_after_cents=after,
                )
                await db.commit()
                await self._emit(
                    {
                        "type": "access.refunded",
                        "attempt_id": str(attempt.id),
                        "method": attempt.method,
                        "fee_cents": attempt.fee_cents,
                        "balance_after_cents": after,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )
                return
            except Exception as exc:
                last_error = str(exc)
                logger.exception("refund_failed attempt_id=%s", attempt.id)
                await asyncio.sleep(0.2)

        refund = await repo.get_refund_by_key(key)
        if refund is not None:
            refund.status = "failed"
            refund.error_code = last_error[:60]
        await repo.transition(
            attempt.id, STATUS_REFUND_PENDING, STATUS_MANUAL_REVIEW, "refund_failed"
        )
        await db.commit()
        await self._alert(
            "RefundFailed",
            {"attempt_id": str(attempt.id), "error": last_error, "chip_id": str(attempt.chip_id)},
        )

    async def _compensate_cash(
        self, repo: AccessAttemptRepository, db: AsyncSession, attempt: AccessAttempt
    ) -> None:
        current = await repo.get(attempt.id)
        assert current is not None
        if current.status == STATUS_DOOR_OPENING:
            await repo.transition(
                current.id,
                STATUS_DOOR_OPENING,
                STATUS_FAILED,
                "door_exhausted",
                failure_reason="door_exhausted",
            )
            await db.commit()
            current = await repo.get(current.id)
            assert current is not None
        if current.status == STATUS_FAILED and current.charge_taken:
            await repo.transition(current.id, STATUS_FAILED, STATUS_REFUND_PENDING, "needs_compensation")
            await db.commit()
            await self._alert(
                "DoorFailedAfterCharge",
                {"attempt_id": str(current.id), "method": "cash", "fee_cents": current.fee_cents},
            )

        await self._cash.restore_fee(str(current.id))
        existing = await repo.get_receipt_for_attempt(current.id)
        if existing is not None:
            await repo.transition(
                current.id,
                STATUS_REFUND_PENDING,
                STATUS_REFUNDED,
                "cash_receipt_issued",
                receipt_id=existing.id,
            )
            await db.commit()
            return

        try:
            code = secrets.token_urlsafe(settings.cash_receipt_code_bytes)
            receipt = await repo.add_receipt(
                CashReceipt(
                    id=uuid.uuid4(),
                    attempt_id=current.id,
                    amount_cents=current.fee_cents,
                    redeem_code=code,
                    status="issued",
                    correlation_id=current.correlation_id,
                )
            )
            key = refund_key(str(current.id))
            await repo.add_refund(
                RefundTransaction(
                    attempt_id=current.id,
                    amount_cents=current.fee_cents,
                    status="succeeded",
                    provider="cash_receipt",
                    idempotency_key=key,
                    receipt_id=receipt.id,
                    correlation_id=current.correlation_id,
                )
            )
            await repo.transition(
                current.id,
                STATUS_REFUND_PENDING,
                STATUS_REFUNDED,
                "cash_receipt_issued",
                receipt_id=receipt.id,
            )
            await db.commit()
            await self._alert(
                "CashReceiptIssued",
                {
                    "attempt_id": str(current.id),
                    "redeem_code": code,
                    "amount_cents": current.fee_cents,
                    "receipt_id": str(receipt.id),
                },
            )
            await self._emit(
                {
                    "type": "access.cash_receipt_issued",
                    "attempt_id": str(current.id),
                    "redeem_code": code,
                    "amount_cents": current.fee_cents,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception:
            logger.exception("cash_receipt_issue_failed attempt_id=%s", current.id)
            await repo.transition(
                current.id, STATUS_REFUND_PENDING, STATUS_MANUAL_REVIEW, "receipt_failed"
            )
            await db.commit()
            await self._alert("RefundFailed", {"attempt_id": str(current.id), "error": "receipt_failed"})

    async def _fail_never_charged(
        self, repo: AccessAttemptRepository, attempt: AccessAttempt, reason: str
    ) -> None:
        try:
            from_status = attempt.status
            if from_status == STATUS_CREATED:
                await repo.transition(
                    attempt.id, STATUS_CREATED, STATUS_FAILED, reason, failure_reason=reason
                )
            elif from_status == STATUS_VALIDATED:
                await repo.transition(
                    attempt.id, STATUS_VALIDATED, STATUS_FAILED, reason, failure_reason=reason
                )
        except TransitionConflictError:
            logger.warning("fail_never_charged_conflict attempt_id=%s", attempt.id)

    async def _emit(self, event: dict) -> None:
        result = self._publish(event)
        if asyncio.iscoroutine(result):
            await result

    async def _alert(self, alert_type: str, payload: dict) -> None:
        event = {
            "type": "access.alert",
            "alert": alert_type,
            **payload,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        await self._emit(event)

    def _decision_from_attempt(self, attempt: AccessAttempt) -> AccessDecisionResponse:
        granted = attempt.status == STATUS_COMPLETED
        return AccessDecisionResponse(
            granted=granted,
            reason="ok" if granted else (attempt.failure_reason or attempt.status.lower()),
            chip_id=str(attempt.chip_id) if attempt.chip_id else None,
            fee_cents=attempt.fee_cents,
            balance_before_cents=attempt.balance_before_cents,
            balance_after_cents=attempt.balance_after_cents,
        )

    async def _legacy_deny(
        self,
        db: AsyncSession,
        *,
        uid: str | None,
        chip_id: str | None,
        reason: str,
        fee: int,
        before: int | None = None,
    ) -> None:
        db.add(
            AccessLog(
                chip_id=chip_id,
                uid=uid,
                decision="denied",
                reason=reason,
                fee_cents=fee,
                balance_before_cents=before,
                balance_after_cents=before,
            )
        )

    async def _legacy_grant_chip(
        self,
        db: AsyncSession,
        *,
        uid: str,
        chip_id: str,
        fee: int,
        before: int,
        after: int,
    ) -> None:
        db.add(
            AccessLog(
                chip_id=chip_id,
                uid=uid,
                decision="granted",
                reason="ok",
                fee_cents=fee,
                balance_before_cents=before,
                balance_after_cents=after,
            )
        )

    async def _legacy_grant_cash(self, db: AsyncSession, *, fee: int, paid_total: int) -> None:
        db.add(
            AccessLog(
                chip_id=None,
                uid=None,
                decision="granted",
                reason="cash_paid",
                fee_cents=fee,
                balance_before_cents=paid_total,
                balance_after_cents=paid_total - fee,
            )
        )

    async def _publish_denied(
        self,
        *,
        uid: str | None,
        chip_id: str | None,
        reason: str,
        fee: int,
        balance: int | None = None,
    ) -> None:
        event: dict = {
            "type": "access.denied",
            "uid": uid,
            "reason": reason,
            "fee_cents": fee,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if chip_id:
            event["chip_id"] = chip_id
        if balance is not None:
            event["balance_cents"] = balance
        await self._emit(event)
