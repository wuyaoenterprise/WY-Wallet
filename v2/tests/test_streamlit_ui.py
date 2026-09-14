from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


FAKE_APP_TEMPLATE = r'''
import pandas as pd
import streamlit as st
import wywallet.web as web

transactions = pd.DataFrame([
    {"id": 1, "date": "2026-08-10", "item": "Petrol", "category": "交通", "type": "Expense", "amount": 75.0, "note": ""},
    {"id": 2, "date": "2026-08-20", "item": "Salary", "category": "收入", "type": "Income", "amount": 3000.0, "note": ""},
    {"id": 3, "date": "2026-08-25", "item": "Petrol refund", "category": "交通", "type": "Refund", "amount": 10.0, "note": ""},
])
transactions["date"] = pd.to_datetime(transactions["date"])
invalid = pd.DataFrame(columns=["id", "date", "item", "category", "type", "amount", "note", "issues"])

web.load_transactions = lambda: transactions.copy()
web.load_invalid_transactions = lambda: invalid.copy()
web._sorted_categories = lambda frame: ["交通", "收入"]
web.load_categories = lambda frame=None: ["交通", "收入"]
web.load_category_rows = lambda: ["交通", "收入"]
web.unregistered_categories = lambda frame=None: []
web.transactions_truncated = lambda: False
web.data_loaded_at = lambda: "2026-09-01T14:00:00+08:00"
web.refresh_data = lambda: None
st.page_link = lambda *args, **kwargs: None

ROUTE = __ROUTE__
if ROUTE is not None:
    original_radio = st.radio
    def fixed_radio(label, options, **kwargs):
        if label == "导航":
            return ROUTE
        return original_radio(label, options, **kwargs)
    st.radio = fixed_radio

web.run()
'''


def _script(route: str | None = None) -> str:
    return FAKE_APP_TEMPLATE.replace("__ROUTE__", repr(route))


def _markdown_texts(at: AppTest) -> list[str]:
    return [str(element.value) for element in at.markdown]


def _unprotected_test_app(route: str | None = None, timeout: int = 25) -> AppTest:
    at = AppTest.from_string(_script(route), default_timeout=timeout)
    at.secrets["ALLOW_UNPROTECTED_ACCESS"] = "true"
    return at


def test_main_app_default_dashboard_renders_without_runtime_exception():
    at = _unprotected_test_app()
    at.run()
    assert not at.exception
    assert any("财务总览" in text for text in _markdown_texts(at))
    assert len(at.metric) >= 5


@pytest.mark.parametrize(
    ("route", "marker"),
    [("交易记录", "交易记录"), ("分析报表", "分析报表"), ("AI 洞察", "AI 洞察"), ("设置与备份", "设置与备份")],
)
def test_each_main_route_smokes_in_fresh_streamlit_session(route: str, marker: str):
    at = _unprotected_test_app(route)
    at.run()
    assert not at.exception, f"route {route} raised: {at.exception}"
    assert any(marker in text for text in _markdown_texts(at)), route


def test_missing_access_configuration_fails_closed():
    at = AppTest.from_string(_script(), default_timeout=20)
    at.run()
    assert not at.exception
    assert any("安全设置未完成" in text for text in _markdown_texts(at))
    assert not any("财务总览" in text for text in _markdown_texts(at))


def test_main_app_password_gate_blocks_data_until_authenticated():
    at = AppTest.from_string(_script(), default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _markdown_texts(at))
    assert len(at.text_input) >= 1
    at.text_input[0].input("test-secret")
    enter = next(button for button in at.button if button.label == "进入")
    enter.click().run()
    assert not at.exception
    assert bool(at.session_state["web_access_ok"]) is True


def test_receipt_page_cannot_bypass_password_gate():
    at = AppTest.from_file("v2/pages/1_📷AI收据识别.py", default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _markdown_texts(at))
    assert any(element.label == "访问密码" for element in at.text_input)
