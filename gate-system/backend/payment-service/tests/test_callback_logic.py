from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.sql.dml import Update

from app.clients import MemberNationalIdMatch, MemberValidation
from app.models import (
    PRODUCT_BALANCE,
    PRODUCT_MONTHLY_SUBSCRIPTION,
    STATUS_ABANDONED,
    STATUS_CREDITING,
    STATUS_PAID,
    STATUS_PENDING,
    CardTopup,
    NedarimCallback,
)
from app.topup_logic import claim_pending_topup, process_nedarim_callback


def member_id_for(uid: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, uid))


@dataclass
class FakeResult:
    rowcount: int


class FakeDb:
    def __init__(self) -> None:
        self.topups: dict[uuid.UUID, CardTopup] = {}
        self.audits: list[NedarimCallback] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, row: object) -> None:
        if isinstance(row, CardTopup):
            self.topups[row.id] = row
        elif isinstance(row, NedarimCallback):
            self.audits.append(row)
        else:
            raise TypeError(type(row))

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, row: object) -> None:
        return None

    async def get(self, model: type, key: object) -> object | None:
        if model is CardTopup:
            assert isinstance(key, uuid.UUID)
            return self.topups.get(key)
        return None

    async def execute(self, stmt: object) -> FakeResult:
        assert isinstance(stmt, Update)
        values = {col.name: value for col, value in stmt._values.items()}  # noqa: SLF001
        new_status = values.get("status")
        if hasattr(new_status, "value"):
            new_status = new_status.value
        matched = 0
        for topup in self.topups.values():
            if topup.status != STATUS_PENDING:
                continue
            # Our claim update always targets a specific id; match any pending
            # row that the test put in the store (tests use one row).
            topup.status = str(new_status)
            matched += 1
            break
        return FakeResult(rowcount=matched)


class FakeFingerprintsClient:
    def __init__(self, balance: int = 0) -> None:
        self.balance = balance
        self.adjustments: list[tuple[str, int, str, str | None]] = []
        self.activations: list[dict] = []
        self.fail = False

    async def lookup_by_national_id(self, national_id: str) -> MemberNationalIdMatch:
        raise ValueError("member_not_found")

    async def validate(self, uid: str) -> MemberValidation:
        raise NotImplementedError

    async def adjust_balance(
        self,
        member_id: str,
        delta_cents: int,
        reason: str,
        description: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        self.adjustments.append((member_id, delta_cents, reason, idempotency_key))
        if self.fail:
            raise RuntimeError("fingerprints-service down")
        # Same key → no second credit.
        keys = [a[3] for a in self.adjustments[:-1]]
        if idempotency_key and idempotency_key in keys:
            return self.balance
        self.balance += delta_cents
        return self.balance

    async def activate_subscription(
        self,
        member_id: str,
        *,
        amount_cents: int,
        nedarim_transaction_id: str,
        hebrew_year: int,
        hebrew_month: int,
        hebrew_month_name: str,
    ) -> int:
        if self.fail:
            raise RuntimeError("fingerprints-service down")
        self.activations.append(
            {
                "member_id": member_id,
                "amount_cents": amount_cents,
                "nedarim_transaction_id": nedarim_transaction_id,
                "hebrew_year": hebrew_year,
                "hebrew_month": hebrew_month,
                "hebrew_month_name": hebrew_month_name,
            }
        )
        return self.balance


def pending_topup(
    *,
    amount_cents: int = 5000,
    created_id: str = "NED-100",
    product: str = PRODUCT_BALANCE,
) -> CardTopup:
    return CardTopup(
        id=uuid.uuid4(),
        member_id=uuid.UUID(member_id_for("FP-001")),
        fingerprint_uid="FP-001",
        amount_cents=amount_cents,
        product=product,
        status=STATUS_PENDING,
        nedarim_created_id=created_id,
        ajax_id="ajax1",
    )


def ok_payload(topup: CardTopup, *, amount: str = "50", transaction_id: str | None = None) -> dict:
    return {
        "TransactionId": transaction_id or topup.nedarim_created_id,
        "Amount": amount,
        "Currency": "1",
        "Confirmation": "CONF",
        "LastNum": "4242",
        "Param1": str(topup.id),
    }


@pytest.mark.asyncio
async def test_claim_pending_moves_status() -> None:
    db = FakeDb()
    topup = pending_topup()
    db.add(topup)
    claimed = await claim_pending_topup(db, topup.id)  # type: ignore[arg-type]
    assert claimed is not None
    assert claimed.status == STATUS_CREDITING
    assert await claim_pending_topup(db, topup.id) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_callback_credits_once() -> None:
    db = FakeDb()
    topup = pending_topup()
    db.add(topup)
    chip = FakeFingerprintsClient(balance=100)
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
        publish=publish,
    )
    assert result.accepted is True
    assert result.http_status == 200
    assert topup.status == STATUS_PAID
    assert topup.balance_after_cents == 5100
    assert topup.last_num == "4242"
    assert chip.adjustments[0][3] == "nedarim:NED-100"
    assert events[0]["type"] == "card_topup.paid"
    assert db.audits[-1].accepted is True


@pytest.mark.asyncio
async def test_duplicate_callback_is_idempotent() -> None:
    db = FakeDb()
    topup = pending_topup()
    db.add(topup)
    chip = FakeFingerprintsClient()

    first = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.194.219.73",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
    )
    second = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.194.219.73",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
    )
    assert first.accepted and second.accepted
    assert second.code == "already_paid"
    assert len(chip.adjustments) == 1
    assert chip.balance == 5000


@pytest.mark.asyncio
async def test_callback_rejects_foreign_ip() -> None:
    db = FakeDb()
    topup = pending_topup()
    db.add(topup)
    chip = FakeFingerprintsClient()

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="8.8.8.8",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
    )
    assert result.accepted is False
    assert result.code == "bad_ip"
    assert result.http_status == 403
    assert topup.status == STATUS_PENDING
    assert chip.adjustments == []
    assert db.audits[-1].rejection_reason == "bad_ip"


@pytest.mark.asyncio
async def test_callback_rejects_amount_mismatch() -> None:
    db = FakeDb()
    topup = pending_topup(amount_cents=5000)
    db.add(topup)

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup, amount="20"),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )
    assert result.code == "amount_mismatch"
    assert topup.status == STATUS_PENDING


@pytest.mark.asyncio
async def test_callback_unknown_topup() -> None:
    db = FakeDb()
    result = await process_nedarim_callback(
        topup_id=uuid.uuid4(),
        payload={"TransactionId": "1", "Amount": "50"},
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )
    assert result.code == "unknown_topup"
    assert result.http_status == 404
    assert db.audits[-1].topup_id is None


@pytest.mark.asyncio
async def test_callback_rejects_foreign_ip_before_topup_lookup() -> None:
    db = FakeDb()
    result = await process_nedarim_callback(
        topup_id=uuid.uuid4(),
        payload={"TransactionId": "1", "Amount": "50"},
        source_ip="1.2.3.4",
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )
    assert result.code == "bad_ip"
    assert result.http_status == 403
    assert db.audits[-1].rejection_reason == "bad_ip"


@pytest.mark.asyncio
async def test_callback_rejects_abandoned() -> None:
    db = FakeDb()
    topup = pending_topup()
    topup.status = STATUS_ABANDONED
    db.add(topup)

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )
    assert result.code == "not_pending"
    assert result.http_status == 409


@pytest.mark.asyncio
async def test_callback_activates_subscription_without_balance_credit() -> None:
    db = FakeDb()
    topup = pending_topup(amount_cents=30000, product=PRODUCT_MONTHLY_SUBSCRIPTION)
    db.add(topup)
    chip = FakeFingerprintsClient(balance=1200)
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup, amount="300"),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
        publish=publish,
    )
    assert result.accepted is True
    assert topup.status == STATUS_PAID
    assert chip.adjustments == []
    assert len(chip.activations) == 1
    assert chip.activations[0]["nedarim_transaction_id"] == "NED-100"
    assert chip.activations[0]["amount_cents"] == 30000
    assert topup.balance_after_cents == 1200
    assert events[0]["type"] == "subscription.paid"
    assert events[0]["product"] == PRODUCT_MONTHLY_SUBSCRIPTION


@pytest.mark.asyncio
async def test_callback_accepts_cleared_id_different_from_create_id() -> None:
    """Live Nedarim CallBack TransactionId can differ from CreateTransaction ID."""
    db = FakeDb()
    topup = pending_topup(created_id="1766827")
    db.add(topup)

    result = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup, transaction_id="76030570"),
        source_ip="18.194.219.73",
        db=db,  # type: ignore[arg-type]
        member_client=FakeFingerprintsClient(),  # type: ignore[arg-type]
    )
    assert result.accepted is True
    assert result.code == "ok"
    assert topup.status == STATUS_PAID
    assert topup.nedarim_transaction_id == "76030570"


@pytest.mark.asyncio
async def test_crediting_retry_after_chip_failure() -> None:
    db = FakeDb()
    topup = pending_topup()
    topup.status = STATUS_CREDITING
    db.add(topup)
    chip = FakeFingerprintsClient()
    chip.fail = True

    failed = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
    )
    assert failed.code == "member_credit_failed"
    assert topup.status == STATUS_CREDITING

    chip.fail = False
    chip.adjustments.clear()
    recovered = await process_nedarim_callback(
        topup_id=topup.id,
        payload=ok_payload(topup),
        source_ip="18.196.146.117",
        db=db,  # type: ignore[arg-type]
        member_client=chip,  # type: ignore[arg-type]
    )
    assert recovered.accepted is True
    assert topup.status == STATUS_PAID
    assert chip.balance == 5000


@pytest.mark.asyncio
async def test_resolve_callback_source_ip_prefers_cf_header() -> None:
    from app.client_ip import resolve_callback_source_ip
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "headers": [
            (b"cf-connecting-ip", b"18.196.146.117"),
            (b"x-real-ip", b"10.0.0.1"),
        ],
        "client": ("127.0.0.1", 123),
        "server": ("127.0.0.1", 80),
        "scheme": "http",
        "query_string": b"",
    }
    request = Request(scope)
    assert resolve_callback_source_ip(request) == "18.196.146.117"
