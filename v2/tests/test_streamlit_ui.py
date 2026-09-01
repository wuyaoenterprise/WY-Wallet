from __future__ import annotations

from streamlit.testing.v1 import AppTest


FAKE_APP = r'''
import pandas as pd
import streamlit as st
import wywallet.web as web

transactions = pd.DataFrame([
    {"id": 1, "date": "2026-08-10", "item": "Petrol", "category": "交通", "type": "Expense", "amount": 75.0, "note": ""},
    {"id": 2, "date": "2026-08-20", "item": "Salary", "category": "收入", "type": "Income", "amount": 3000.0, "note": ""},
])
transactions["date"] = pd.to_datetime(transactions["date"])
invalid = pd.DataFrame(columns=["id", "date", "item", "category", "type", "amount", "note", "issues"])

web.load_transactions = lambda: transactions.copy()
web.load_invalid_transactions = lambda: invalid.copy()
web._sorted_categories = lambda frame: ["交通", "收入"]
web.transactions_truncated = lambda: False
web.data_loaded_at = lambda: "2026-09-01T14:00:00+08:00"
web.refresh_data = lambda: None
st.page_link = lambda *args, **kwargs: None

web.run()
'''


def _markdown_texts(at: AppTest) -> list[str]:
    return [str(element.value) for element in at.markdown]


def test_main_app_renders_and_navigation_switches_without_runtime_exception():
    at = AppTest.from_string(FAKE_APP, default_timeout=20)
    at.run()
    assert not at.exception
    assert any("财务总览" in text for text in _markdown_texts(at))
    assert len(at.metric) >= 5

    navigation = next(radio for radio in at.radio if "总览" in list(radio.options))
    navigation.set_value("交易记录").run()
    assert not at.exception
    assert any("交易记录" in text for text in _markdown_texts(at))


def test_main_app_password_gate_blocks_data_until_authenticated():
    at = AppTest.from_string(FAKE_APP, default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _markdown_texts(at))
    assert len(at.text_input) >= 1

    at.text_input[0].input("test-secret")
    enter = next(button for button in at.button if button.label == "进入")
    enter.click().run()
    assert not at.exception
    assert any("财务总览" in text for text in _markdown_texts(at))


def test_receipt_page_cannot_bypass_password_gate():
    at = AppTest.from_file("v2/pages/1_📷AI收据识别.py", default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _markdown_texts(at))
    assert any(element.label == "访问密码" for element in at.text_input)
