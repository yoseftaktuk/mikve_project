"""Unit tests for CashSession overpay discard and method-switch clear."""

from __future__ import annotations

import pytest

from app.access_logic import CashSession

FEE = 1500


@pytest.mark.asyncio
async def test_try_pay_discards_overpay_and_zeros_balance() -> None:
    session = CashSession(timeout_seconds=0)
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    session.set_publish(publish)

    await session.add(600)
    await session.add(1000)
    result = await session.try_pay(fee_cents=FEE, attempt_id="attempt-1")

    assert result.ok is True
    assert result.paid_total_cents == 1600
    assert session.accumulated_cents == 0
    reset = next(e for e in events if e.get("type") == "cash.reset")
    assert reset["reason"] == "overpay_discarded"
    assert reset["discarded_cents"] == 100
    assert reset["fee_cents"] == FEE
    assert reset["previous_total_cents"] == 1600


@pytest.mark.asyncio
async def test_try_pay_exact_fee_leaves_zero_without_overpay_event() -> None:
    session = CashSession(timeout_seconds=0)
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    session.set_publish(publish)

    await session.add(FEE)
    result = await session.try_pay(fee_cents=FEE, attempt_id="attempt-exact")

    assert result.ok is True
    assert result.paid_total_cents == FEE
    assert session.accumulated_cents == 0
    assert not any(e.get("reason") == "overpay_discarded" for e in events)


@pytest.mark.asyncio
async def test_clear_for_other_method_clears_partial_cash() -> None:
    session = CashSession(timeout_seconds=0)
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    session.set_publish(publish)

    await session.add(600)
    cleared = await session.clear_for_other_method()

    assert cleared == 600
    assert session.accumulated_cents == 0
    reset = next(e for e in events if e.get("type") == "cash.reset")
    assert reset["reason"] == "method_switched"
    assert reset["previous_total_cents"] == 600


@pytest.mark.asyncio
async def test_clear_for_other_method_noop_when_empty() -> None:
    session = CashSession(timeout_seconds=0)
    assert await session.clear_for_other_method() == 0
    assert session.accumulated_cents == 0


@pytest.mark.asyncio
async def test_restore_fee_restores_fee_only_not_discarded_overage() -> None:
    session = CashSession(timeout_seconds=0)
    await session.add(1600)
    result = await session.try_pay(fee_cents=FEE, attempt_id="attempt-restore")
    assert result.ok is True
    assert session.accumulated_cents == 0

    restored = await session.restore_fee("attempt-restore")
    assert restored is True
    assert session.accumulated_cents == FEE


@pytest.mark.asyncio
async def test_try_pay_idempotent_for_same_attempt() -> None:
    session = CashSession(timeout_seconds=0)
    await session.add(1600)
    first = await session.try_pay(fee_cents=FEE, attempt_id="same")
    second = await session.try_pay(fee_cents=FEE, attempt_id="same")
    assert first.ok is True
    assert second.ok is True
    assert second.paid_total_cents == first.paid_total_cents == 1600
    assert session.accumulated_cents == 0
