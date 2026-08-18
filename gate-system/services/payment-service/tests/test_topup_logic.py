from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from app.clients import MemberValidation
from app.models import (
    PRODUCT_MONTHLY_SUBSCRIPTION,
    STATUS_ABANDONED,
    STATUS_FAILED,
    STATUS_PENDING,
    CardTopup,
)
from app.nedarim_plus import CreateTransactionCommand, CreateTransactionResult, NedarimError
from app.topup_logic import abandon_card_topup, create_card_topup, get_card_topup
from gate_shared.errors import AppError


def member_id_for(uid: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, uid))


class FakeDb:
    """Minimal AsyncSession stand-in for top-up create/status/abandon."""

    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, CardTopup] = {}
        self.commits = 0

    def add(self, row: object) -> None:
        assert isinstance(row, CardTopup)
        self.rows[row.id] = row

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row: object) -> None:
        return None

    async def get(self, model: type, key: object) -> object | None:
        assert model is CardTopup
        assert isinstance(key, uuid.UUID)
        return self.rows.get(key)


class FakeFingerprintsClient:
    def __init__(self, chips: dict[str, MemberValidation] | None = None) -> None:
        self.chips = chips or {}

    async def validate(self, uid: str) -> MemberValidation:
        if uid not in self.chips:
            raise ValueError("member_not_found")
        return self.chips[uid]


@dataclass
class FakePaymentProvider:
    result: CreateTransactionResult | None = None
    error: NedarimError | None = None
    calls: list[CreateTransactionCommand] | None = None
    iframe_url_value: str = "https://www.matara.pro/nedarimplus/iframe/"

    def __post_init__(self) -> None:
        self.calls = []

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def iframe_url(self) -> str:
        return self.iframe_url_value

    async def create_transaction(self, command: CreateTransactionCommand) -> CreateTransactionResult:
        assert self.calls is not None
        self.calls.append(command)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture
def public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "nedarim")
    monkeypatch.setattr("app.topup_logic.settings.public_base_url", "https://gate.example.org")
    monkeypatch.setattr("app.topup_logic.settings.nedarim_groupe", "כניסה")
    monkeypatch.setattr("app.topup_logic.settings.nedarim_iframe_url", "https://www.matara.pro/nedarimplus/iframe/")
    monkeypatch.setattr("app.topup_logic.settings.topup_amounts_cents", "2000,5000,10000")
    monkeypatch.setattr("app.topup_logic.settings.subscription_price_cents", 30000)


def enabled_chip(uid: str = "FP-001", balance: int = 0) -> MemberValidation:
    return MemberValidation(
        member_id=member_id_for(uid),
        uid=uid,
        is_enabled=True,
        balance_cents=balance,
        holder_name="Test",
    )


@pytest.mark.asyncio
async def test_create_card_topup_happy_path(public_url: None) -> None:
    chip = enabled_chip()
    db = FakeDb()
    nedarim = FakePaymentProvider(result=CreateTransactionResult(transaction_id="NED-1"))

    created = await create_card_topup(
        fingerprint_uid=chip.uid,
        amount_cents=5000,
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
        payment_provider=nedarim,  # type: ignore[arg-type]
    )

    assert created.nedarim_transaction_id == "NED-1"
    assert created.amount_cents == 5000
    assert created.iframe_url.endswith("/iframe/")
    assert db.commits == 1
    stored = db.rows[created.topup_id]
    assert stored.status == STATUS_PENDING
    assert stored.nedarim_created_id == "NED-1"
    assert stored.ajax_id
    assert nedarim.calls is not None and len(nedarim.calls) == 1
    command = nedarim.calls[0]
    assert command.amount_cents == 5000
    assert command.param1 == str(created.topup_id)
    assert f"/api/payments/nedarim/callback/{created.topup_id}" in command.callback_url
    assert command.callback_url.endswith("nid=[ID]")
    assert command.groupe == "כניסה"


@pytest.mark.asyncio
async def test_create_rejects_amount_outside_presets(public_url: None) -> None:
    chip = enabled_chip()
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=1234,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "invalid_amount"


@pytest.mark.asyncio
async def test_create_rejects_unknown_chip(public_url: None) -> None:
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid="FP-999",
            amount_cents=2000,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "member_not_found"


@pytest.mark.asyncio
async def test_create_rejects_disabled_chip(public_url: None) -> None:
    chip = MemberValidation(
        member_id=member_id_for("FP-002"),
        uid="FP-002",
        is_enabled=False,
        balance_cents=0,
    )
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=2000,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "member_disabled"


@pytest.mark.asyncio
async def test_create_requires_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.topup_logic.settings.payment_mode", "nedarim")
    monkeypatch.setattr("app.topup_logic.settings.public_base_url", "")
    monkeypatch.setattr("app.topup_logic.settings.topup_amounts_cents", "2000,5000,10000")
    chip = enabled_chip()
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=2000,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "public_base_url_missing"


@pytest.mark.asyncio
async def test_create_marks_failed_when_nedarim_rejects(public_url: None) -> None:
    chip = enabled_chip()
    db = FakeDb()
    nedarim = FakePaymentProvider(error=NedarimError("nedarim_rejected", "institution closed"))

    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=2000,
            db=db,  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=nedarim,  # type: ignore[arg-type]
        )
    assert exc.value.code == "nedarim_rejected"
    assert len(db.rows) == 1
    stored = next(iter(db.rows.values()))
    assert stored.status == STATUS_FAILED
    assert stored.error_code == "nedarim_rejected"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_get_and_abandon(public_url: None) -> None:
    chip = enabled_chip()
    db = FakeDb()
    created = await create_card_topup(
        fingerprint_uid=chip.uid,
        amount_cents=2000,
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
        payment_provider=FakePaymentProvider(result=CreateTransactionResult("NED-2")),  # type: ignore[arg-type]
    )
    loaded = await get_card_topup(created.topup_id, db)  # type: ignore[arg-type]
    assert loaded.status == STATUS_PENDING

    abandoned = await abandon_card_topup(created.topup_id, db)  # type: ignore[arg-type]
    assert abandoned.status == STATUS_ABANDONED

    with pytest.raises(AppError) as exc:
        await abandon_card_topup(created.topup_id, db)  # type: ignore[arg-type]
    assert exc.value.code == "topup_not_pending"


@pytest.mark.asyncio
async def test_create_subscription_purchase(public_url: None) -> None:
    chip = enabled_chip()
    db = FakeDb()
    nedarim = FakePaymentProvider(result=CreateTransactionResult(transaction_id="NED-SUB"))

    created = await create_card_topup(
        fingerprint_uid=chip.uid,
        amount_cents=30000,
        product=PRODUCT_MONTHLY_SUBSCRIPTION,
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
        payment_provider=nedarim,  # type: ignore[arg-type]
    )

    assert created.product == PRODUCT_MONTHLY_SUBSCRIPTION
    assert created.amount_cents == 30000
    stored = db.rows[created.topup_id]
    assert stored.product == PRODUCT_MONTHLY_SUBSCRIPTION
    assert nedarim.calls is not None
    assert "subscription" in nedarim.calls[0].comment


@pytest.mark.asyncio
async def test_create_subscription_rejects_wrong_amount(public_url: None) -> None:
    chip = enabled_chip()
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=2000,
            product=PRODUCT_MONTHLY_SUBSCRIPTION,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "invalid_amount"


@pytest.mark.asyncio
async def test_create_subscription_rejects_when_already_active(public_url: None) -> None:
    chip = MemberValidation(
        member_id=member_id_for("FP-001"),
        uid="FP-001",
        is_enabled=True,
        balance_cents=0,
        subscription_active=True,
        subscription_month_name="אב",
    )
    with pytest.raises(AppError) as exc:
        await create_card_topup(
            fingerprint_uid=chip.uid,
            amount_cents=30000,
            product=PRODUCT_MONTHLY_SUBSCRIPTION,
            db=FakeDb(),  # type: ignore[arg-type]
            member_client=FakeFingerprintsClient({chip.uid: chip}),  # type: ignore[arg-type]
            payment_provider=FakePaymentProvider(result=CreateTransactionResult("x")),  # type: ignore[arg-type]
        )
    assert exc.value.code == "subscription_already_active"
