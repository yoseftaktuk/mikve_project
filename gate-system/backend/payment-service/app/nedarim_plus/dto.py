from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PAYMENT_TYPE_REGULAR = "Ragil"


@dataclass(frozen=True)
class CreateTransactionCommand:
    """Everything that varies per transaction.

    Institution credentials are deliberately absent: they belong to the client,
    so no caller can accidentally send a transaction as a different institution.
    """

    amount_cents: int
    callback_url: str
    ajax_id: str
    param1: str
    comment: str = ""
    groupe: str = ""
    tashlumim: int = 1
    currency: int = 1


@dataclass(frozen=True)
class CreateTransactionResult:
    transaction_id: str


def shekels_from_cents(amount_cents: int) -> str:
    """Render an agorot amount the way Nedarim expects Amount: in shekels.

    Whole shekels are sent without a fractional part, matching the examples in
    the documentation.
    """
    shekels = Decimal(amount_cents) / Decimal(100)
    if shekels == shekels.to_integral_value():
        return str(int(shekels))
    return f"{shekels:.2f}"
