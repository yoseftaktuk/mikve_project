from __future__ import annotations

import pytest

from app.nedarim_plus import (
    CreateTransactionCommand,
    NedarimError,
    build_create_transaction_form,
    parse_create_transaction_response,
    shekels_from_cents,
)


def test_shekels_from_cents_whole_shekels():
    assert shekels_from_cents(2000) == "20"
    assert shekels_from_cents(5000) == "50"
    assert shekels_from_cents(10000) == "100"


def test_shekels_from_cents_fractional():
    assert shekels_from_cents(1550) == "15.50"
    assert shekels_from_cents(1) == "0.01"


def test_build_create_transaction_form_matches_docs():
    command = CreateTransactionCommand(
        amount_cents=5000,
        callback_url="https://gate.example.org/api/payments/nedarim/callback/abc?nid=[ID]",
        ajax_id="1710000000123abc",
        param1="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        comment="gate top-up FP-001",
        groupe="כניסה",
    )
    form = build_create_transaction_form(command, mosad="7018669", api_valid="xxxxxxxxxx")
    assert form == {
        "Mosad": "7018669",
        "ApiValid": "xxxxxxxxxx",
        "PaymentType": "Ragil",
        "Amount": "50",
        "Tashlumim": "1",
        "Currency": "1",
        "Groupe": "כניסה",
        "Comment": "gate top-up FP-001",
        "Param1": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "CallBack": "https://gate.example.org/api/payments/nedarim/callback/abc?nid=[ID]",
        "AjaxId": "1710000000123abc",
    }


def test_parse_create_transaction_ok():
    result = parse_create_transaction_response('{"Status":"OK","ID":"998877"}')
    assert result.transaction_id == "998877"


def test_parse_create_transaction_error_status():
    with pytest.raises(NedarimError) as exc:
        parse_create_transaction_response('{"Status":"Error","Message":"missing amount"}')
    assert exc.value.code == "nedarim_rejected"
    assert "missing amount" in exc.value.message


def test_parse_create_transaction_non_json():
    with pytest.raises(NedarimError) as exc:
        parse_create_transaction_response("OK without json")
    assert exc.value.code == "nedarim_bad_response"


def test_parse_create_transaction_ok_without_id():
    with pytest.raises(NedarimError) as exc:
        parse_create_transaction_response('{"Status":"OK"}')
    assert exc.value.code == "nedarim_bad_response"
