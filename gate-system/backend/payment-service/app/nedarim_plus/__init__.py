"""Nedarim Plus integration.

Everything that knows about matara.pro lives here. The rest of the service
talks to this package through the dataclasses below and never sees a URL, a
form field, or an httpx call.
"""

from .callback import (
    NEDARIM_CALLBACK_IPS,
    NedarimCallbackPayload,
    assert_callback_source_ip,
    parse_callback_payload,
    verify_callback,
)
from .client import (
    NedarimPlusClient,
    build_create_transaction_form,
    parse_create_transaction_response,
)
from .dto import CreateTransactionCommand, CreateTransactionResult, shekels_from_cents
from .errors import NedarimError

__all__ = [
    "NEDARIM_CALLBACK_IPS",
    "CreateTransactionCommand",
    "CreateTransactionResult",
    "NedarimCallbackPayload",
    "NedarimError",
    "NedarimPlusClient",
    "assert_callback_source_ip",
    "build_create_transaction_form",
    "parse_callback_payload",
    "parse_create_transaction_response",
    "shekels_from_cents",
    "verify_callback",
]
