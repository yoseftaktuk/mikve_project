from __future__ import annotations

import pytest

from app.nedarim_plus import NedarimError, parse_callback_payload, verify_callback


def test_parse_webhook_shape():
    parsed = parse_callback_payload(
        {
            "TransactionId": "12345",
            "Amount": "50",
            "Currency": "1",
            "Confirmation": "ABC123",
            "LastNum": "4242",
            "Param1": "topup-uuid",
        }
    )
    assert parsed.transaction_id == "12345"
    assert parsed.amount_cents == 5000
    assert parsed.currency == 1
    assert parsed.confirmation == "ABC123"
    assert parsed.last_num == "4242"
    assert parsed.param1 == "topup-uuid"


def test_parse_transaction_response_shape():
    parsed = parse_callback_payload(
        {
            "Status": "OK",
            "ID": "99",
            "Amount": 20,
            "Confirmation": "",
            "LastNum": "1111",
        }
    )
    assert parsed.transaction_id == "99"
    assert parsed.amount_cents == 2000
    assert parsed.confirmation is None
    assert parsed.last_num == "1111"


def test_parse_rejects_error_status():
    with pytest.raises(NedarimError) as exc:
        parse_callback_payload({"Status": "Error", "ID": "1", "Amount": "10"})
    assert exc.value.code == "not_success"


def test_parse_rejects_fractional_agora():
    with pytest.raises(NedarimError) as exc:
        parse_callback_payload({"TransactionId": "1", "Amount": "10.001"})
    assert exc.value.code == "bad_amount"


def test_verify_accepts_documented_ip_and_amount():
    payload = {"TransactionId": "7", "Amount": "100", "Currency": "1"}
    parsed = verify_callback(
        payload=payload,
        source_ip="18.196.146.117",
        expected_amount_cents=10000,
        expected_topup_id=None,
    )
    assert parsed.transaction_id == "7"


def test_verify_rejects_foreign_ip():
    with pytest.raises(NedarimError) as exc:
        verify_callback(
            payload={"TransactionId": "7", "Amount": "50"},
            source_ip="1.2.3.4",
            expected_amount_cents=5000,
        )
    assert exc.value.code == "bad_ip"


def test_verify_rejects_amount_mismatch():
    with pytest.raises(NedarimError) as exc:
        verify_callback(
            payload={"TransactionId": "7", "Amount": "20"},
            source_ip="18.194.219.73",
            expected_amount_cents=5000,
        )
    assert exc.value.code == "amount_mismatch"


def test_verify_rejects_param_mismatch_when_present():
    with pytest.raises(NedarimError) as exc:
        verify_callback(
            payload={"TransactionId": "7", "Amount": "50", "Param1": "other"},
            source_ip="18.194.219.73",
            expected_amount_cents=5000,
            expected_topup_id="expected-id",
        )
    assert exc.value.code == "param_mismatch"


def test_verify_allows_missing_param1():
    parsed = verify_callback(
        payload={"TransactionId": "7", "Amount": "50"},
        source_ip="18.194.219.73",
        expected_amount_cents=5000,
        expected_topup_id="expected-id",
    )
    assert parsed.param1 is None
