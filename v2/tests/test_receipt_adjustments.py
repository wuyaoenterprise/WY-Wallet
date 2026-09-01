from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import wywallet.db as db
from wywallet.config import EXPENSE, REFUND
from wywallet.receipt import evaluate_receipt_candidates, materialize_receipt_adjustments, reconcile_receipt_total


def test_receipt_metadata_becomes_visible_ledger_rows_and_reconciles_to_total():
    rows = materialize_receipt_adjustments(
        [{"date": "2026-08-01", "item": "餐点", "category": "饮食", "type": EXPENSE, "amount": 100, "note": ""}],
        tax=6,
        service_charge=10,
        discount=5,
        fallback_category="其他",
    )
    assert [(row["item"], row["type"], row["amount"]) for row in rows[1:]] == [
        ("收据税费", EXPENSE, 6.0),
        ("收据服务费", EXPENSE, 10.0),
        ("收据折扣", REFUND, 5.0),
    ]

    edited = pd.DataFrame([{**row, "保存": True, "日期已确认": True, "仍然保存重复": False} for row in rows])
    statuses, candidates = evaluate_receipt_candidates(edited, set())
    assert all(status == "可保存" for status in statuses)
    reconciliation = reconcile_receipt_total(candidates, 111.0)
    assert reconciliation["expected_total"] == pytest.approx(111.0)
    assert reconciliation["matches"] is True


def test_adjustment_rows_inherit_unreadable_date_and_still_require_confirmation():
    rows = materialize_receipt_adjustments(
        [{"date": None, "item": "餐点", "category": "饮食", "type": EXPENSE, "amount": 100, "note": ""}],
        tax=6,
    )
    assert rows[-1]["date"] is None


def test_refund_adjustment_remains_shared_table_compatible():
    logical = db.normalize_transaction({
        "date": date(2026, 8, 1), "item": "收据折扣", "category": "饮食",
        "type": REFUND, "amount": 5, "note": "",
    })
    physical = db._encode_transaction_for_db(logical)
    assert physical["type"] == EXPENSE
    assert physical["amount"] == pytest.approx(-5)
