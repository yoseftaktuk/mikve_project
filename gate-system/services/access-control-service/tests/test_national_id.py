"""Unit tests for Israeli national ID validation."""

from __future__ import annotations

import pytest

from gate_shared.national_id import InvalidNationalIdError, is_valid_israeli_id, normalize_national_id


def test_valid_israeli_ids() -> None:
    assert is_valid_israeli_id("123456782")
    assert is_valid_israeli_id("000000018")
    assert normalize_national_id("123456782") == "123456782"
    assert normalize_national_id("18") == "000000018"


def test_invalid_israeli_ids() -> None:
    assert not is_valid_israeli_id("123456789")
    assert not is_valid_israeli_id("123")
    assert not is_valid_israeli_id("abcdefghj")
    with pytest.raises(InvalidNationalIdError):
        normalize_national_id("123456789")
    with pytest.raises(InvalidNationalIdError):
        normalize_national_id("")
    with pytest.raises(InvalidNationalIdError):
        normalize_national_id("12a")
