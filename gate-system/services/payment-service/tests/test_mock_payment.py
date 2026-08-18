from __future__ import annotations

import pytest

from app.models import STATUS_PAID
from app.nedarim_plus import CreateTransactionResult
from app.topup_logic import create_card_topup, simulate_mock_card_payment
from tests.test_callback_logic import FakeFingerprintsClient, FakeDb, pending_topup
from tests.test_topup_logic import FakeDb as CreateFakeDb
from tests.test_topup_logic import FakeFingerprintsClient as CreateFakeFingerprintsClient
from tests.test_topup_logic import FakePaymentProvider, enabled_chip


@pytest.mark.asyncio
async def test_create_card_topup_mock_without_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "mock")
    monkeypatch.setattr("app.topup_logic.settings.public_base_url", "")
    monkeypatch.setattr("app.topup_logic.settings.topup_amounts_cents", "2000,5000,10000")

    chip = enabled_chip()
    db = CreateFakeDb()
    provider = FakePaymentProvider(
        result=CreateTransactionResult(transaction_id="MOCK-abc"),
        iframe_url_value="",
    )

    created = await create_card_topup(
        fingerprint_uid=chip.uid,
        amount_cents=2000,
        db=db,  # type: ignore[arg-type]
        member_client=CreateFakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
        payment_provider=provider,  # type: ignore[arg-type]
    )

    assert created.nedarim_transaction_id == "MOCK-abc"
    assert created.iframe_url == ""
    assert provider.calls is not None
    assert provider.calls[0].callback_url.startswith("mock://")


@pytest.mark.asyncio
async def test_simulate_pay_credits_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "mock")

    db = FakeDb()
    topup = pending_topup(amount_cents=2000, created_id="MOCK-pay-1")
    db.add(topup)

    member_client = FakeFingerprintsClient(balance=100)
    result = await simulate_mock_card_payment(
        topup_id=topup.id,
        db=db,  # type: ignore[arg-type]
        member_client=member_client,  # type: ignore[arg-type]
    )

    assert result.accepted is True
    stored = db.topups[topup.id]
    assert stored.status == STATUS_PAID
    assert stored.balance_after_cents == 2100
    assert len(member_client.adjustments) == 1


@pytest.mark.asyncio
async def test_simulate_pay_rejected_in_nedarim_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "nedarim")

    db = FakeDb()
    topup = pending_topup()
    db.add(topup)

    result = await simulate_mock_card_payment(
        topup_id=topup.id,
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )

    assert result.accepted is False
    assert result.code == "mock_only"
    assert result.http_status == 403


@pytest.mark.asyncio
async def test_simulate_pay_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "mock")

    db = FakeDb()
    topup = pending_topup(amount_cents=5000, created_id="MOCK-idem")
    db.add(topup)

    member_client = FakeFingerprintsClient(balance=0)
    first = await simulate_mock_card_payment(
        topup_id=topup.id,
        db=db,  # type: ignore[arg-type]
        member_client=member_client,  # type: ignore[arg-type]
    )
    second = await simulate_mock_card_payment(
        topup_id=topup.id,
        db=db,  # type: ignore[arg-type]
        member_client=member_client,  # type: ignore[arg-type]
    )

    assert first.accepted is True
    assert second.accepted is True
    assert second.code == "already_paid"
    assert len(member_client.adjustments) == 1
