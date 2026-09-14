from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import wywallet.db as db
from wywallet.ai import FinanceQueryPlan, authoritative_summary_markdown, execute_finance_plan
from wywallet.config import EXPENSE, INCOME, REFUND, REFUND_DB_MARKER


def frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "id" not in df:
        df["id"] = range(1, len(df) + 1)
    return df


def test_refund_is_encoded_without_requiring_negative_amount(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    logical = db.normalize_transaction({"date": "2026-08-01", "item": "Return", "category": "购物", "type": REFUND, "amount": 100, "note": "merchant refund"})
    physical = db._encode_transaction_for_db(logical)
    assert physical["type"] == INCOME
    assert physical["amount"] == pytest.approx(100)
    assert physical["note"].startswith(REFUND_DB_MARKER)
    normalized, issues = db._normalize_loaded_row({"id": 1, **physical})
    assert issues == []
    assert normalized["type"] == REFUND
    assert normalized["amount"] == pytest.approx(100)
    assert normalized["note"] == "merchant refund"


def test_old_negative_expense_refund_remains_readable(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    normalized, issues = db._normalize_loaded_row({"id": 1, "date": "2026-08-01", "item": "Old refund", "category": "购物", "type": EXPENSE, "amount": -100, "note": ""})
    assert issues == []
    assert normalized["type"] == REFUND
    assert normalized["amount"] == pytest.approx(100)


def test_authoritative_summary_can_show_matched_scope():
    df = frame([
        {"date": "2026-08-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 80},
        {"date": "2026-08-02", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 70},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="specific", subject="油费",
        aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-08-01", date_to="2026-08-31", matched_items=["打油"],
    )
    result = execute_finance_plan(plan, df)
    text = authoritative_summary_markdown(result)
    assert "打油" in text
    assert "Petrol" in text
    assert "RM 150.00" in text
