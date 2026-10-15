from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .callback import _amount_to_cents
from .errors import NedarimError


@dataclass(frozen=True)
class NedarimWebhookFields:
    """Fields extracted from an institution webhook. Extra JSON keys are ignored."""

    transaction_id: str | None
    zeout_raw: str | None
    zeout_normalized: str | None
    groupe: str | None
    amount_cents: int | None
    amount_error: str | None
    currency: int | None
    currency_error: str | None
    transaction_time: str | None
    confirmation: str | None
    transaction_type: str | None
    raw: dict[str, Any]


def _raw_str(payload: dict[str, Any], key: str, *, strip: bool) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is None:
        return None
    text = str(value)
    if strip:
        text = text.strip()
    return text if text else None


def normalize_zeout(raw: str | None) -> str | None:
    """Keep digits only and left-pad to 9. Reject empty or over-length values."""
    if raw is None:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits or len(digits) > 9:
        return None
    return digits.zfill(9)


def redact_webhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy the payload and keep only the last four of LastNum."""
    redacted = dict(payload)
    last_num = redacted.get("LastNum")
    if last_num is not None:
        text = str(last_num)
        redacted["LastNum"] = text[-4:] if text else text
    return redacted


def parse_webhook_fields(payload: dict[str, Any]) -> NedarimWebhookFields:
    """Parse known fields without rejecting unknown keys."""
    if not isinstance(payload, dict):
        raise NedarimError("bad_payload", "Webhook body is not a JSON object")

    transaction_id = _raw_str(payload, "TransactionId", strip=True)
    zeout_raw = _raw_str(payload, "Zeout", strip=True)
    # Groupe is compared exactly; do not strip.
    groupe = _raw_str(payload, "Groupe", strip=False)

    amount_cents: int | None = None
    amount_error: str | None = None
    raw_amount = payload.get("Amount") if "Amount" in payload else None
    if raw_amount is None or str(raw_amount).strip() == "":
        amount_error = "missing_amount"
    else:
        try:
            amount_cents = _amount_to_cents(raw_amount)
        except NedarimError as exc:
            amount_error = exc.code

    currency: int | None = None
    currency_error: str | None = None
    currency_raw = _raw_str(payload, "Currency", strip=True)
    if currency_raw is None:
        currency = 1
    else:
        try:
            currency = int(currency_raw)
        except ValueError:
            currency_error = "unreadable_currency"

    return NedarimWebhookFields(
        transaction_id=transaction_id,
        zeout_raw=zeout_raw,
        zeout_normalized=normalize_zeout(zeout_raw),
        groupe=groupe,
        amount_cents=amount_cents,
        amount_error=amount_error,
        currency=currency,
        currency_error=currency_error,
        transaction_time=_raw_str(payload, "TransactionTime", strip=True),
        confirmation=_raw_str(payload, "Confirmation", strip=True),
        transaction_type=_raw_str(payload, "TransactionType", strip=True),
        raw=payload,
    )
