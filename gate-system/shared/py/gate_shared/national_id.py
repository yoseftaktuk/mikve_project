"""Israeli national ID (תעודת זהות) normalization and check-digit validation."""

from __future__ import annotations


class InvalidNationalIdError(ValueError):
    """Raised when a national ID fails format or checksum validation."""

    def __init__(self, message: str = "invalid_national_id") -> None:
        super().__init__(message)


def normalize_national_id(raw: str) -> str:
    """Return a 9-digit national ID or raise InvalidNationalIdError.

    Accepts digits only. Values shorter than 9 digits are left-padded with zeros
    after confirming they consist of digits; the result must pass the checksum.
    """
    value = (raw or "").strip()
    if not value or not value.isdigit():
        raise InvalidNationalIdError("invalid_national_id")
    if len(value) > 9:
        raise InvalidNationalIdError("invalid_national_id")
    padded = value.zfill(9)
    if not is_valid_israeli_id(padded):
        raise InvalidNationalIdError("invalid_national_id")
    return padded


def is_valid_israeli_id(national_id: str) -> bool:
    """Return True when national_id is exactly 9 digits with a valid check digit."""
    if len(national_id) != 9 or not national_id.isdigit():
        return False
    total = 0
    for i, ch in enumerate(national_id):
        num = int(ch) * ((i % 2) + 1)
        total += num if num < 10 else num - 9
    return total % 10 == 0
