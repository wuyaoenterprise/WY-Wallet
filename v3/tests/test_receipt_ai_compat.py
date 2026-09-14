from __future__ import annotations

import inspect
import json

import pytest

import wywallet


def test_receipt_schema_has_no_gemini_incompatible_numeric_keywords():
    schema = wywallet._ReceiptResultCompat.model_json_schema()
    packed = json.dumps(schema, sort_keys=True)
    assert "exclusiveMinimum" not in packed
    assert '"minimum"' not in packed


def test_receipt_validation_stays_local_and_rejects_bad_money():
    with pytest.raises(Exception):
        wywallet._ReceiptTransactionCompat(
            date="2026-09-06", item="商品", category="购物", type="Expense", amount=0
        )
    with pytest.raises(Exception):
        wywallet._ReceiptResultCompat(transactions=[], tax=-0.01)


def test_receipt_recognition_keeps_v2_style_and_chinese_output_contract():
    source = inspect.getsource(wywallet._recognize_receipt_v2_style)
    assert "response_schema" not in source
    assert "HttpOptions" not in source
    assert "item、note、warnings 默认输出简体中文" in source
    assert "商家/品牌专有名称尽量保留收据原文" in source
    assert "Translate item, note and warnings to concise Simplified Chinese" in source


def test_receipt_parser_accepts_raw_and_fenced_json():
    payload = {
        "merchant": "NISK TRADE CITY",
        "receipt_number": "12345",
        "transactions": [
            {
                "date": "2026-09-05",
                "item": "香菇",
                "category": "饮食",
                "type": "Expense",
                "amount": 6.68,
                "note": "",
            }
        ],
        "receipt_total": 6.68,
        "tax": 0,
        "service_charge": 0,
        "discount": 0,
        "warnings": [],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    assert wywallet._decode_receipt_result(raw).transactions[0].item == "香菇"
    fenced = f"```json\n{raw}\n```"
    assert wywallet._decode_receipt_result(fenced).receipt_number == "12345"


def test_receipt_parser_keeps_validation_errors_short_for_ui():
    with pytest.raises(RuntimeError, match="字段不完整或金额格式异常"):
        wywallet._decode_receipt_result(
            json.dumps(
                {
                    "merchant": None,
                    "receipt_number": None,
                    "transactions": [
                        {
                            "date": "2026-09-05",
                            "item": "商品",
                            "category": "购物",
                            "type": "Expense",
                            "amount": 0,
                            "note": "",
                        }
                    ],
                    "receipt_total": 0,
                    "tax": 0,
                    "service_charge": 0,
                    "discount": 0,
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )


def test_friendly_gemini_errors_do_not_dump_raw_provider_payloads():
    assert "当前繁忙" in str(wywallet._friendly_receipt_error(Exception("503 UNAVAILABLE high demand")))
    assert "请求额度" in str(wywallet._friendly_receipt_error(Exception("429 resource exhausted")))
    assert "响应超时" in str(wywallet._friendly_receipt_error(Exception("read operation timed out")))
    long_error = wywallet._friendly_receipt_error(Exception("x" * 1000))
    assert len(str(long_error)) < 230
