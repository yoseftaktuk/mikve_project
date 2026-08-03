from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import NedarimError

# Source addresses Nedarim documents for CallBack delivery.
# Docs: אימות תשלום ואבטחה (v=95) / מערכת Callback (v=6).
NEDARIM_CALLBACK_IPS: frozenset[str] = frozenset({"18.196.146.117", "18.194.219.73"})


@dataclass(frozen=True)
class NedarimCallbackPayload:
    """Fields we need from a successful-transaction callback.

    The documentation describes two shapes (TransactionResponse vs webhook
    chapter). Association goes through our URL path; these fields are only
    used for amount/cross-checks and display.
    """

    transaction_id: str
    amount_cents: int
    currency: int
    confirmation: str | None
    last_num: str | None
    param1: str | None
    raw: dict[str, Any]


def assert_callback_source_ip(source_ip: str | None) -> None:
    """Reject updates that did not come from a documented Nedarim address."""
    if not source_ip or source_ip not in NEDARIM_CALLBACK_IPS:
        raise NedarimError("bad_ip", f"Callback source IP is not allowed: {source_ip!r}")


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _amount_to_cents(raw_amount: Any) -> int:
    """Convert Nedarim Amount (shekels) to agorot without float drift."""
    try:
        shekels = Decimal(str(raw_amount).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise NedarimError("bad_amount", f"Unreadable Amount: {raw_amount!r}") from exc
    cents = shekels * Decimal(100)
    if cents != cents.to_integral_value():
        raise NedarimError("bad_amount", f"Amount is not a whole agora: {raw_amount!r}")
    value = int(cents)
    if value <= 0:
        raise NedarimError("bad_amount", f"Amount must be positive: {raw_amount!r}")
    return value


def parse_callback_payload(payload: dict[str, Any]) -> NedarimCallbackPayload:
    """Parse either documented callback body shape into a single DTO.

    Accepts Status/ID/Confirmation (iframe TransactionResponse) or
    TransactionId/Amount/Currency (webhook chapter). Param1 is optional —
    the docs mention it for security cross-check but neither table lists it.
    """
    if not isinstance(payload, dict):
        raise NedarimError("bad_payload", "Callback body is not a JSON object")

    # Success-only delivery is documented for the iframe CallBack; a Status
    # field of Error (if ever sent) must not credit a balance.
    status = _first_str(payload, "Status")
    if status is not None and status.upper() != "OK":
        raise NedarimError("not_success", f"Callback Status is not OK: {status}")

    transaction_id = _first_str(payload, "TransactionId", "ID", "Id")
    if not transaction_id:
        raise NedarimError("bad_payload", "Callback is missing TransactionId/ID")

    raw_amount = payload.get("Amount")
    if raw_amount is None or str(raw_amount).strip() == "":
        raise NedarimError("bad_payload", "Callback is missing Amount")
    amount_cents = _amount_to_cents(raw_amount)

    currency_raw = _first_str(payload, "Currency") or "1"
    try:
        currency = int(currency_raw)
    except ValueError as exc:
        raise NedarimError("bad_payload", f"Unreadable Currency: {currency_raw!r}") from exc

    confirmation = _first_str(payload, "Confirmation")
    last_num = _first_str(payload, "LastNum")
    if last_num is not None:
        last_num = last_num[-4:]

    return NedarimCallbackPayload(
        transaction_id=transaction_id,
        amount_cents=amount_cents,
        currency=currency,
        confirmation=confirmation,
        last_num=last_num,
        param1=_first_str(payload, "Param1", "param1"),
        raw=payload,
    )


def verify_callback(
    *,
    payload: dict[str, Any],
    source_ip: str | None,
    expected_amount_cents: int,
    expected_topup_id: str | None = None,
) -> NedarimCallbackPayload:
    """Full documentation-recommended check before crediting a balance.

    1. Source IP is one of Nedarim's documented addresses.
    2. Body parses into a known successful-transaction shape.
    3. Amount matches what we created the transaction with.
    4. Optional Param1 matches our correlation id when present.
    """
    assert_callback_source_ip(source_ip)
    parsed = parse_callback_payload(payload)

    if parsed.amount_cents != expected_amount_cents:
        raise NedarimError(
            "amount_mismatch",
            f"Callback amount {parsed.amount_cents} != expected {expected_amount_cents}",
        )

    if expected_topup_id and parsed.param1 and parsed.param1 != expected_topup_id:
        raise NedarimError(
            "param_mismatch",
            f"Callback Param1 {parsed.param1!r} != expected topup id {expected_topup_id!r}",
        )

    return parsed
