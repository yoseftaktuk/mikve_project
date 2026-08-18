from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy.exc import IntegrityError

from app.clients import MemberNationalIdMatch
from app.models import (
    PRODUCT_BALANCE,
    STATUS_PAID,
    WEBHOOK_DUPLICATE,
    WEBHOOK_FAILED,
    WEBHOOK_IGNORED_CATEGORY,
    WEBHOOK_INVALID,
    WEBHOOK_PROCESSED,
    WEBHOOK_USER_UNRESOLVED,
    CardTopup,
    NedarimWebhookEvent,
)
from app.hebrew_calendar import current_hebrew_month
from app.nedarim_plus.webhook import normalize_zeout, parse_webhook_fields, redact_webhook_payload
from app.webhook_logic import process_nedarim_webhook

NEDARIM_IP = "18.196.146.117"
TARGET_GROUPE = "מנוי מקווה חודש"
BALANCE_GROUPE = "ערך צבור למקווה"
SYNAGOGUE_GROUPE = "תרומה לבית הכנסת"
TEST_ZEOUT = "123456789"


def member_id_for(uid: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, uid))


class FakeWebhookDb:
    """In-memory stand-in: per-task pending rows (like separate sessions) plus a shared unique index."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending_by_task: dict[int, list[object]] = {}
        self.events: dict[str, NedarimWebhookEvent] = {}
        self.topups_by_txn: dict[str, CardTopup] = {}
        self.commits = 0
        self.rollbacks = 0

    def _task_key(self) -> int:
        task = asyncio.current_task()
        return id(task) if task is not None else 0

    def _pending(self) -> list[object]:
        return self._pending_by_task.setdefault(self._task_key(), [])

    def add(self, row: object) -> None:
        self._pending().append(row)

    async def commit(self) -> None:
        async with self._lock:
            pending = self._pending()
            for row in pending:
                if isinstance(row, NedarimWebhookEvent):
                    if row.transaction_id in self.events:
                        raise IntegrityError("UNIQUE", {}, Exception("duplicate"))
                    self.events[row.transaction_id] = row
                elif isinstance(row, CardTopup) and row.nedarim_transaction_id:
                    self.topups_by_txn[row.nedarim_transaction_id] = row
            pending.clear()
            self.commits += 1

    async def rollback(self) -> None:
        self._pending().clear()
        self.rollbacks += 1

    async def scalar(self, stmt: object) -> object | None:
        entity = stmt.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
        compiled = stmt.compile()  # type: ignore[attr-defined]
        value = next(iter(compiled.params.values()), None)
        if entity is NedarimWebhookEvent:
            return self.events.get(value)
        if entity is CardTopup:
            return self.topups_by_txn.get(value)
        return None


@dataclass
class FakeWebhookFingerprintsClient:
    balance: int = 10_000
    chips_by_national_id: dict[str, MemberNationalIdMatch] = field(default_factory=dict)
    adjustments: list[tuple[str, int, str, str | None]] = field(default_factory=list)
    activations: list[dict] = field(default_factory=list)
    fail: bool = False
    already_active: bool = False

    async def lookup_by_national_id(self, national_id: str) -> MemberNationalIdMatch:
        match = self.chips_by_national_id.get(national_id)
        if match is None:
            raise ValueError("member_not_found")
        return match

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
        keys = [item[3] for item in self.adjustments[:-1]]
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
        if self.already_active:
            raise ValueError("subscription_already_active")
        existing = [a for a in self.activations if a["nedarim_transaction_id"] == nedarim_transaction_id]
        if existing:
            return self.balance
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


def seeded_client(*, balance: int = 10_000, zeout: str = TEST_ZEOUT) -> FakeWebhookFingerprintsClient:
    member_id = member_id_for("FP-001")
    client = FakeWebhookFingerprintsClient(balance=balance)
    client.chips_by_national_id[zeout] = MemberNationalIdMatch(
        member_id=member_id,
        uid="FP-001",
        is_enabled=True,
        balance_cents=balance,
        national_id=zeout,
    )
    return client


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "TransactionId": "TEST-001",
        "Zeout": TEST_ZEOUT,
        "Amount": "50",
        "Currency": "1",
        "Groupe": TARGET_GROUPE,
    }
    body.update(overrides)
    return body


async def handle(
    body: dict[str, object],
    *,
    db: FakeWebhookDb | None = None,
    member_client: FakeWebhookFingerprintsClient | None = None,
    source_ip: str | None = NEDARIM_IP,
    skip_source_ip: bool = False,
):
    db = db or FakeWebhookDb()
    member_client = member_client or seeded_client()
    result = await process_nedarim_webhook(
        payload=body,
        source_ip=source_ip,
        db=db,  # type: ignore[arg-type]
        member_client=member_client,  # type: ignore[arg-type]
        skip_source_ip=skip_source_ip,
    )
    return result, db, member_client


@pytest.mark.asyncio
async def test_successful_target_donation() -> None:
    result, db, client = await handle(payload())
    hebrew = current_hebrew_month()
    assert result.code == WEBHOOK_PROCESSED
    assert result.http_status == 200
    assert client.balance == 10_000
    assert client.adjustments == []
    event = db.events["TEST-001"]
    assert event.processing_status == WEBHOOK_PROCESSED
    assert event.zeout == TEST_ZEOUT
    assert len(client.activations) == 1
    activation = client.activations[0]
    assert activation["nedarim_transaction_id"] == "TEST-001"
    assert activation["amount_cents"] == 5000
    assert activation["hebrew_year"] == hebrew.year
    assert activation["hebrew_month"] == hebrew.month
    assert activation["hebrew_month_name"] == hebrew.name


@pytest.mark.asyncio
async def test_stored_value_groupe_credits_balance() -> None:
    result, db, client = await handle(
        payload(TransactionId="TEST-BAL-001", Groupe=BALANCE_GROUPE)
    )
    assert result.code == WEBHOOK_PROCESSED
    assert result.http_status == 200
    assert client.balance == 15_000
    assert client.activations == []
    assert len(client.adjustments) == 1
    member_id, delta, reason, key = client.adjustments[0]
    assert delta == 5000
    assert reason == "nedarim_webhook"
    assert key == "nedarim:TEST-BAL-001"
    assert db.events["TEST-BAL-001"].processing_status == WEBHOOK_PROCESSED


@pytest.mark.asyncio
async def test_synagogue_donation_is_not_allowed() -> None:
    result, db, client = await handle(
        payload(TransactionId="TEST-SYN-001", Groupe=SYNAGOGUE_GROUPE)
    )
    assert result.code == WEBHOOK_IGNORED_CATEGORY
    assert result.http_status == 200
    assert client.adjustments == []
    assert client.activations == []
    event = db.events["TEST-SYN-001"]
    assert event.processing_status == WEBHOOK_IGNORED_CATEGORY
    assert event.processing_error == "not_allowed_groupe"


@pytest.mark.asyncio
async def test_not_allowed_groupe_retry_stays_ignored() -> None:
    db = FakeWebhookDb()
    client = seeded_client()
    db.events["TEST-SYN-RETRY"] = NedarimWebhookEvent(
        transaction_id="TEST-SYN-RETRY",
        zeout=TEST_ZEOUT,
        groupe=SYNAGOGUE_GROUPE,
        amount_cents=5000,
        currency=1,
        processing_status=WEBHOOK_IGNORED_CATEGORY,
        processing_error="not_allowed_groupe",
        raw_payload={},
    )
    result, _, _ = await handle(
        payload(TransactionId="TEST-SYN-RETRY", Groupe=SYNAGOGUE_GROUPE),
        db=db,
        member_client=client,
    )
    assert result.code == WEBHOOK_IGNORED_CATEGORY
    assert client.adjustments == []
    assert db.events["TEST-SYN-RETRY"].processing_error == "not_allowed_groupe"


@pytest.mark.asyncio
async def test_ignored_category_retry_credits_when_groupe_now_matches() -> None:
    db = FakeWebhookDb()
    client = seeded_client()
    db.events["TEST-RETRY"] = NedarimWebhookEvent(
        transaction_id="TEST-RETRY",
        zeout=TEST_ZEOUT,
        groupe=BALANCE_GROUPE,
        amount_cents=5000,
        currency=1,
        processing_status=WEBHOOK_IGNORED_CATEGORY,
        processing_error="groupe_mismatch",
        raw_payload={},
    )
    result, _, _ = await handle(
        payload(TransactionId="TEST-RETRY", Groupe=BALANCE_GROUPE),
        db=db,
        member_client=client,
    )
    assert result.code == WEBHOOK_PROCESSED
    assert len(client.adjustments) == 1
    assert client.adjustments[0][1] == 5000
    assert db.events["TEST-RETRY"].processing_status == WEBHOOK_PROCESSED


@pytest.mark.asyncio
async def test_stored_value_duplicate_credits_once() -> None:
    db = FakeWebhookDb()
    client = seeded_client()
    body = payload(TransactionId="TEST-BAL-DUP", Groupe=BALANCE_GROUPE)
    first, _, _ = await handle(body, db=db, member_client=client)
    second, _, _ = await handle(body, db=db, member_client=client)
    assert first.code == WEBHOOK_PROCESSED
    assert second.code == WEBHOOK_DUPLICATE
    assert client.balance == 15_000
    assert len(client.adjustments) == 1
    assert client.activations == []


@pytest.mark.asyncio
async def test_wrong_category_does_not_credit() -> None:
    result, db, client = await handle(payload(TransactionId="TEST-002", Groupe="בניין"))
    assert result.code == WEBHOOK_IGNORED_CATEGORY
    assert client.balance == 10_000
    assert db.events["TEST-002"].processing_status == WEBHOOK_IGNORED_CATEGORY
    assert client.activations == []
    assert client.adjustments == []


@pytest.mark.asyncio
async def test_groupe_trailing_space_is_not_a_match() -> None:
    result, _, client = await handle(payload(TransactionId="TEST-002b", Groupe=f"{TARGET_GROUPE} "))
    assert result.code == WEBHOOK_IGNORED_CATEGORY
    assert client.balance == 10_000
    assert client.activations == []


@pytest.mark.asyncio
async def test_missing_zeout() -> None:
    body = payload(TransactionId="TEST-003")
    del body["Zeout"]
    result, db, client = await handle(body)
    assert result.code == WEBHOOK_USER_UNRESOLVED
    assert client.balance == 10_000
    assert db.events["TEST-003"].processing_status == WEBHOOK_USER_UNRESOLVED
    assert db.events["TEST-003"].processing_error == "missing_zeout"


@pytest.mark.asyncio
async def test_unknown_zeout() -> None:
    result, db, client = await handle(payload(TransactionId="TEST-004", Zeout="999999999"))
    assert result.code == WEBHOOK_USER_UNRESOLVED
    assert client.balance == 10_000
    assert db.events["TEST-004"].processing_error == "unknown_zeout"


@pytest.mark.asyncio
async def test_duplicate_transaction_id_activates_once() -> None:
    db = FakeWebhookDb()
    client = seeded_client()
    first, _, _ = await handle(payload(TransactionId="TEST-005"), db=db, member_client=client)
    second, _, _ = await handle(payload(TransactionId="TEST-005"), db=db, member_client=client)
    assert first.code == WEBHOOK_PROCESSED
    assert second.code == WEBHOOK_DUPLICATE
    assert client.balance == 10_000
    assert len(client.activations) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_activates_once() -> None:
    db = FakeWebhookDb()
    client = seeded_client()
    body = payload(TransactionId="TEST-006")
    results = await asyncio.gather(
        handle(body, db=db, member_client=client),
        handle(body, db=db, member_client=client),
    )
    codes = {item[0].code for item in results}
    assert WEBHOOK_PROCESSED in codes
    assert client.balance == 10_000
    assert len(client.activations) == 1
    assert client.activations[0]["nedarim_transaction_id"] == "TEST-006"


@pytest.mark.parametrize("amount", ["", "abc", "-50"])
@pytest.mark.asyncio
async def test_invalid_amount(amount: str) -> None:
    result, db, client = await handle(payload(TransactionId=f"TEST-007-{amount}", Amount=amount))
    assert result.code == WEBHOOK_INVALID
    assert result.http_status == 400
    assert client.balance == 10_000
    assert client.activations == []


@pytest.mark.asyncio
async def test_unsupported_currency() -> None:
    result, db, client = await handle(payload(TransactionId="TEST-008", Currency="2"))
    assert result.code == WEBHOOK_INVALID
    assert client.balance == 10_000
    assert db.events["TEST-008"].processing_error == "unsupported_currency"


@pytest.mark.asyncio
async def test_unauthorized_source_rejected() -> None:
    result, db, client = await handle(payload(TransactionId="TEST-009"), source_ip="1.2.3.4")
    assert result.code == "bad_ip"
    assert result.http_status == 403
    assert client.balance == 10_000
    assert db.events == {}
    assert client.activations == []


@pytest.mark.asyncio
async def test_extra_json_fields_are_accepted() -> None:
    result, _, client = await handle(
        payload(TransactionId="TEST-EXTRA", FutureField="yes", Comments="keep")
    )
    assert result.code == WEBHOOK_PROCESSED
    assert client.balance == 10_000
    assert len(client.activations) == 1


@pytest.mark.asyncio
async def test_kiosk_topup_collision_does_not_credit_again() -> None:
    db = FakeWebhookDb()
    db.topups_by_txn["TEST-KIOSK"] = CardTopup(
        id=uuid.uuid4(),
        member_id=uuid.UUID(member_id_for("FP-001")),
        fingerprint_uid="FP-001",
        amount_cents=5000,
        product=PRODUCT_BALANCE,
        status=STATUS_PAID,
        nedarim_transaction_id="TEST-KIOSK",
        ajax_id="ajax1",
    )
    result, _, client = await handle(payload(TransactionId="TEST-KIOSK"), db=db)
    assert result.code == WEBHOOK_DUPLICATE
    assert client.balance == 10_000
    assert client.activations == []


@pytest.mark.asyncio
async def test_missing_transaction_id_is_invalid() -> None:
    body = payload()
    del body["TransactionId"]
    result, db, client = await handle(body)
    assert result.code == WEBHOOK_INVALID
    assert result.http_status == 400
    assert client.balance == 10_000
    assert db.events == {}


@pytest.mark.asyncio
async def test_ambiguous_zeout_does_not_credit() -> None:
    client = seeded_client()

    async def ambiguous(_national_id: str) -> MemberNationalIdMatch:
        raise ValueError("national_id_ambiguous")

    client.lookup_by_national_id = ambiguous  # type: ignore[method-assign]
    result, db, _ = await handle(payload(TransactionId="TEST-AMBIG"), member_client=client)
    assert result.code == WEBHOOK_USER_UNRESOLVED
    assert db.events["TEST-AMBIG"].processing_error == "ambiguous_zeout"
    assert client.balance == 10_000
    assert client.activations == []


@pytest.mark.asyncio
async def test_already_active_subscription_fails_without_balance_change() -> None:
    client = seeded_client()
    client.already_active = True
    result, db, _ = await handle(payload(TransactionId="TEST-ACTIVE"), member_client=client)
    assert result.code == WEBHOOK_FAILED
    assert result.http_status == 500
    assert db.events["TEST-ACTIVE"].processing_error == "subscription_already_active"
    assert client.balance == 10_000
    assert client.activations == []


def test_normalize_zeout_pads_digits() -> None:
    assert normalize_zeout("123456789") == "123456789"
    assert normalize_zeout("12345678") == "012345678"
    assert normalize_zeout(None) is None
    assert normalize_zeout("abc") is None


def test_redact_last_num() -> None:
    redacted = redact_webhook_payload({"LastNum": "1234567890123456", "Amount": "50"})
    assert redacted["LastNum"] == "3456"


def test_parse_keeps_unknown_fields() -> None:
    fields = parse_webhook_fields(
        {
            "TransactionId": "X",
            "Amount": "10",
            "Currency": "1",
            "Groupe": TARGET_GROUPE,
            "NewNedarimField": "ok",
        }
    )
    assert fields.transaction_id == "X"
    assert fields.raw["NewNedarimField"] == "ok"
    assert fields.amount_cents == 1000
