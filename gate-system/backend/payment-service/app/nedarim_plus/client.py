from __future__ import annotations

import json
import logging

import httpx

from ..settings import settings
from .dto import (
    PAYMENT_TYPE_REGULAR,
    CreateTransactionCommand,
    CreateTransactionResult,
    shekels_from_cents,
)
from .errors import NedarimError

logger = logging.getLogger(__name__)

CREATE_TRANSACTION_ACTION = "CreateTransaction"


def build_create_transaction_form(
    command: CreateTransactionCommand, *, mosad: str, api_valid: str
) -> dict[str, str]:
    """Exact form body for Action=CreateTransaction.

    CallBackMailError is not sent: the documented parameter list for this
    endpoint does not include it, and the real institution does not require it.
    Delivery-failure alerts therefore reach the institution administrators.
    """
    return {
        "Mosad": mosad,
        "ApiValid": api_valid,
        "PaymentType": PAYMENT_TYPE_REGULAR,
        "Amount": shekels_from_cents(command.amount_cents),
        "Tashlumim": str(command.tashlumim),
        "Currency": str(command.currency),
        "Groupe": command.groupe,
        "Comment": command.comment,
        "Param1": command.param1,
        "CallBack": command.callback_url,
        "AjaxId": command.ajax_id,
    }


def parse_create_transaction_response(body: str) -> CreateTransactionResult:
    """Read the CreateTransaction reply.

    Some Nedarim endpoints answer in plain text rather than JSON, so an
    unparseable body is reported as a protocol error instead of crashing.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        raise NedarimError("nedarim_bad_response", f"Non-JSON reply: {body[:200]}") from None

    if not isinstance(payload, dict):
        raise NedarimError("nedarim_bad_response", f"Unexpected reply shape: {body[:200]}")

    if str(payload.get("Status", "")).strip().upper() != "OK":
        message = str(payload.get("Message") or "").strip() or "Nedarim Plus rejected the transaction"
        raise NedarimError("nedarim_rejected", message)

    transaction_id = str(payload.get("ID") or "").strip()
    if not transaction_id:
        raise NedarimError("nedarim_bad_response", "Status OK without a transaction id")

    return CreateTransactionResult(transaction_id=transaction_id)


class NedarimPlusClient:
    """The only place in the system that speaks HTTP to Nedarim Plus."""

    def __init__(self) -> None:
        self._url = settings.nedarim_api_url
        self._mosad = settings.nedarim_mosad
        self._api_valid = settings.nedarim_api_valid
        self._timeout = settings.nedarim_timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self._mosad and self._api_valid)

    async def create_transaction(self, command: CreateTransactionCommand) -> CreateTransactionResult:
        """Open a transaction and return the id the iframe needs to clear it.

        The amount is fixed here, on the server, so the kiosk cannot change what
        the cardholder is charged.
        """
        if not self.is_configured:
            raise NedarimError(
                "nedarim_not_configured", "NEDARIM_MOSAD and NEDARIM_API_VALID are not set"
            )

        form = build_create_transaction_form(command, mosad=self._mosad, api_valid=self._api_valid)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url, params={"Action": CREATE_TRANSACTION_ACTION}, data=form
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise NedarimError("nedarim_unreachable", f"Could not reach Nedarim Plus: {exc}") from exc

        result = parse_create_transaction_response(response.text)
        logger.info("nedarim_transaction_created id=%s amount_cents=%s", result.transaction_id, command.amount_cents)
        return result
