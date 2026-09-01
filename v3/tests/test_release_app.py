from __future__ import annotations

from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "v3" / "app.py"

FAKE_APP = r'''
import runpy
import pandas as pd
import streamlit as st
import wywallet.db as db
import wywallet.web as web

transactions = pd.DataFrame([
    {"id": 1, "date": "2026-08-10", "item": "Petrol", "category": "交通", "type": "Expense", "amount": 75.0, "note": "", "receipt_id": ""},
    {"id": 2, "date": "2026-08-20", "item": "Salary", "category": "收入", "type": "Income", "amount": 3000.0, "note": "", "receipt_id": ""},
    {"id": 3, "date": "2026-08-25", "item": "Petrol refund", "category": "交通", "type": "Refund", "amount": 10.0, "note": "", "receipt_id": ""},
])
transactions["date"] = pd.to_datetime(transactions["date"])
invalid = pd.DataFrame(columns=["id", "date", "item", "category", "type", "amount", "note", "issues"])

db.load_transactions = lambda: transactions.copy()
db.load_invalid_transactions = lambda: invalid.copy()
db.transactions_truncated = lambda: __TRUNCATED__
db.data_loaded_at = lambda: "2026-09-01T14:00:00+08:00"
db.refresh_data = lambda: None
web.load_transactions = db.load_transactions
web.load_invalid_transactions = db.load_invalid_transactions
web._sorted_categories = lambda frame: ["交通", "收入"]
if __TRUNCATED__:
    def forbidden_dashboard(frame):
        raise RuntimeError("PARTIAL_DASHBOARD_RENDERED")
    web._dashboard = forbidden_dashboard
else:
    web._dashboard = lambda frame: st.write("DASHBOARD_OK")
web._transactions_page = lambda frame, categories: st.write("TRANSACTIONS_OK")
web._reports_page = lambda frame, invalid_rows: st.write("REPORTS_OK")
web._ai_page = lambda frame: st.write("AI_OK")
web._settings_page = lambda frame, invalid_rows, categories: st.write("SETTINGS_OK")
web.add_transaction_dialog = lambda categories: None
st.page_link = lambda *args, **kwargs: None

ROUTE = __ROUTE__
if ROUTE is not None:
    original_radio = st.radio
    def fixed_radio(label, options, **kwargs):
        if label == "导航":
            return ROUTE
        return original_radio(label, options, **kwargs)
    st.radio = fixed_radio

runpy.run_path(r"__APP__", run_name="__main__")
'''


def _script(route=None, truncated=False):
    return (
        FAKE_APP
        .replace("__APP__", str(APP).replace("\\", "\\\\"))
        .replace("__ROUTE__", repr(route))
        .replace("__TRUNCATED__", repr(bool(truncated)))
    )


def _texts(at: AppTest) -> list[str]:
    return [str(element.value) for element in at.markdown]


def _errors(at: AppTest) -> list[str]:
    return [str(element.value) for element in at.error]


def test_actual_v3_entrypoint_renders_dashboard_with_fake_database():
    at = AppTest.from_string(_script(), default_timeout=25)
    at.secrets["ALLOW_UNPROTECTED_ACCESS"] = "true"
    at.run()
    assert not at.exception
    assert any("DASHBOARD_OK" in text for text in _texts(at))


def test_actual_v3_entrypoint_routes_to_fragment_page():
    at = AppTest.from_string(_script("交易记录"), default_timeout=25)
    at.secrets["ALLOW_UNPROTECTED_ACCESS"] = "true"
    at.run()
    assert not at.exception
    assert any("TRANSACTIONS_OK" in text for text in _texts(at))


def test_missing_access_configuration_fails_closed_before_database_read():
    at = AppTest.from_file(str(APP), default_timeout=20)
    at.run()
    assert not at.exception
    assert any("安全设置未完成" in text for text in _texts(at))


def test_password_gate_is_rendered_before_database_read():
    at = AppTest.from_file(str(APP), default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _texts(at))
    assert any(element.label == "访问密码" for element in at.text_input)


def test_database_failure_is_visible_instead_of_blank_screen():
    script = _script().replace(
        'db.load_transactions = lambda: transactions.copy()',
        'def fail_load():\n    raise RuntimeError("SUPABASE_TEST_FAILURE")\ndb.load_transactions = fail_load',
    )
    at = AppTest.from_string(script, default_timeout=20)
    at.secrets["ALLOW_UNPROTECTED_ACCESS"] = "true"
    at.run()
    assert not at.exception
    assert any("无法连接财务数据库" in text for text in _texts(at))
    assert any("SUPABASE_TEST_FAILURE" in text for text in _errors(at))


def test_truncated_ledger_does_not_render_partial_dashboard_totals():
    at = AppTest.from_string(_script(truncated=True), default_timeout=20)
    at.secrets["ALLOW_UNPROTECTED_ACCESS"] = "true"
    at.run()
    assert not at.exception


def test_receipt_page_cannot_bypass_password_gate():
    receipt_page = ROOT / "v3" / "pages" / "receipt.py"
    at = AppTest.from_file(str(receipt_page), default_timeout=20)
    at.secrets["WEB_ACCESS_PASSWORD"] = "test-secret"
    at.run()
    assert not at.exception
    assert any("WY Wallet 私人访问" in text for text in _texts(at))
