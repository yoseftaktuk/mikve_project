from gate_shared.errors import AppError


def charge_credit_card(*, amount: float) -> None:
    """Stub credit-card provider. Replace with Stripe/Adyen in production."""
    if amount < 1:
        raise AppError(
            code="invalid_amount",
            message="Amount must be at least 1.",
            http_status=400,
        )
