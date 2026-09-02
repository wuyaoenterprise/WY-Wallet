from __future__ import annotations

from datetime import date

import pandas as pd

from wywallet.config import EXPENSE, INCOME, REFUND
from wywallet.ledger_codec import logical_type, physical_payload
from wywallet.receipt import materialize_receipt_adjustments
from wywallet.reports_page import _pie_with_other
from wywallet.snapshot import _normalize_payload
from wywallet.ux import exact_duplicate_count, ranked_categories


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def test_categories_are_ranked_by_real_usage_then_name():
    transactions = _frame([
        {"date": "2026-09-01", "category": "交通", "item": "A", "type": EXPENSE, "amount": 1},
        {"date": "2026-09-01", "category": "饮食", "item": "B", "type": EXPENSE, "amount": 1},
        {"date": "2026-09-02", "category": "交通", "item": "C", "type": EXPENSE, "amount": 1},
    ])
    assert ranked_categories(["购物", "饮食", "交通"], transactions) == ["交通", "饮食", "购物"]


def test_manual_duplicate_detection_is_exact_and_non_destructive():
    transactions = _frame([
        {"date": "2026-09-02", "category": "午餐", "item": "杂饭", "type": EXPENSE, "amount": 9.0},
        {"date": "2026-09-02", "category": "午餐", "item": "杂饭", "type": EXPENSE, "amount": 10.0},
    ])
    count = exact_duplicate_count(
        transactions,
        tx_date=date(2026, 9, 2),
        item="杂饭",
        category="午餐",
        tx_type=EXPENSE,
        amount=9.0,
    )
    assert count == 1
    assert len(transactions) == 2


def test_structured_refund_payload_preserves_receipt_metadata():
    payload = physical_payload({
        "date": "2026-09-02",
        "item": "折扣",
        "category": "饮食",
        "type": REFUND,
        "amount": 5.0,
        "note": "promo",
        "receipt_id": "abcdef1234567890-002",
    }, "receipt_discount")
    assert payload["type"] == INCOME
    assert payload["receipt_id"] == "abcdef1234567890-002"
    assert payload["flow_subtype"] == "receipt_discount"
    assert logical_type(INCOME, "receipt_discount") == REFUND


def test_receipt_adjustments_assign_structured_flow_subtypes():
    rows = materialize_receipt_adjustments(
        [{"date": "2026-09-02", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 20}],
        tax=1.2,
        service_charge=2.0,
        discount=3.0,
        receipt_id="abcdef1234567890",
    )
    subtypes = {row.get("flow_subtype") for row in rows}
    assert "receipt_tax" in subtypes
    assert "receipt_service_charge" in subtypes
    assert "receipt_discount" in subtypes


def test_snapshot_decodes_structured_refund_without_relying_on_note_marker():
    payload = {
        "transactions": [{
            "id": 1,
            "date": "2026-09-02",
            "item": "Receipt discount",
            "category": "饮食",
            "type": INCOME,
            "amount": 3.0,
            "note": "plain note",
            "updated_at": "2026-09-02T00:00:00+00:00",
            "receipt_id": "abcdef1234567890-002",
            "flow_subtype": "receipt_discount",
            "client_token": None,
        }],
        "categories": [{"name": "饮食"}],
        "total_count": 1,
        "revision": 2,
        "revision_updated_at": "2026-09-02T00:00:00+00:00",
    }
    snapshot = _normalize_payload(payload)
    row = snapshot["transactions"].iloc[0]
    assert row["type"] == REFUND
    assert row["receipt_id"] == "abcdef1234567890-002"
    assert row["flow_subtype"] == "receipt_discount"


def test_report_pie_keeps_full_positive_denominator_with_other_bucket():
    frame = pd.DataFrame({
        "category": [f"C{i}" for i in range(10)],
        "amount": [100.0 - i for i in range(10)],
    })
    pie = _pie_with_other(frame, top_n=8)
    assert len(pie) == 9
    assert "其余类别" in pie["category"].tolist()
    assert round(float(pie["amount"].sum()), 2) == round(float(frame["amount"].sum()), 2)
