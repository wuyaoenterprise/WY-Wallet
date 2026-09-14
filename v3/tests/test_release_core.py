from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE, INCOME, REFUND
from wywallet.exporting import sanitize_spreadsheet_text
from wywallet.receipt import ReceiptCandidate, finalize_receipt_candidates
from wywallet.receipt_identity import add_line_ids, receipt_already_exists, receipt_root, receipt_root_id


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    if "id" not in frame:
        frame["id"] = range(1, len(frame) + 1)
    if "note" not in frame:
        frame["note"] = ""
    if "receipt_id" not in frame:
        frame["receipt_id"] = ""
    return frame


def test_refund_reduces_expense_not_income():
    frame = _frame([
        {"date": "2026-08-01", "item": "Salary", "category": "收入", "type": INCOME, "amount": 3000},
        {"date": "2026-08-02", "item": "Phone", "category": "购物", "type": EXPENSE, "amount": 1000},
        {"date": "2026-08-03", "item": "Phone refund", "category": "购物", "type": REFUND, "amount": 300},
    ])
    income, net_expense, balance = analytics.calculate_totals(frame)
    assert income == pytest.approx(3000)
    assert net_expense == pytest.approx(700)
    assert balance == pytest.approx(2300)


def test_current_year_ai_monthly_average_excludes_incomplete_month(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    frame = _frame([
        {"date": "2026-01-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 800},
        {"date": "2026-08-10", "item": "B", "category": "其他", "type": EXPENSE, "amount": 800},
        {"date": "2026-09-01", "item": "C", "category": "其他", "type": EXPENSE, "amount": 900},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-01-01", date_to="2026-09-01",
    )
    assert ai.execute_finance_plan(plan, frame)["authoritative_total"] == pytest.approx(200.0)


def test_max_transaction_returns_real_transaction(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    frame = _frame([
        {"id": 1, "date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 20},
        {"id": 2, "date": "2026-08-03", "item": "Car loan", "category": "交通", "type": EXPENSE, "amount": 581},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="max_transaction",
        flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31",
    )
    result = ai.execute_finance_plan(plan, frame)
    assert result["authoritative_total"] == pytest.approx(581)
    assert result["extreme_transaction"]["item"] == "Car loan"


def test_custom_comparison_is_calculated_locally(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    frame = _frame([
        {"date": "2026-06-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 150},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31",
        comparison="custom", comparison_date_from="2026-06-01", comparison_date_to="2026-06-30",
    )
    result = ai.execute_finance_plan(plan, frame)
    assert result["authoritative_total"] == pytest.approx(150)
    assert result["comparison"]["value"] == pytest.approx(100)
    assert result["comparison"]["delta"] == pytest.approx(50)


def test_malaysia_semantic_aliases_work_locally():
    frame = _frame([
        {"date": "2026-08-01", "item": "TNB", "category": "居住", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-02", "item": "Unifi Home", "category": "居住", "type": EXPENSE, "amount": 120},
        {"date": "2026-08-03", "item": "Prudential", "category": "保险", "type": EXPENSE, "amount": 200},
    ])
    assert "TNB" in ai._fallback_subject_matches("电费", frame)[0]
    assert "Unifi Home" in ai._fallback_subject_matches("网费", frame)[0]
    assert "Prudential" in ai._fallback_subject_matches("保险", frame)[0]


def test_receipt_number_identity_is_photo_independent():
    payload = {"merchant": "ABC Cafe", "receipt_number": "R-123", "receipt_total": 53.0}
    rows = [{"date": "2026-09-01", "item": "Meal", "type": EXPENSE, "amount": 53}]
    first = receipt_root_id(payload, rows)
    second = receipt_root_id(dict(payload), list(rows))
    assert first == second
    assert len(first) == 16


def test_no_number_identity_uses_line_signature_to_avoid_same_total_collision():
    payload = {"merchant": "ABC Cafe", "receipt_number": None, "receipt_total": 20.0}
    first = receipt_root_id(payload, [{"date": "2026-09-01", "item": "Lunch", "type": EXPENSE, "amount": 20}])
    second = receipt_root_id(payload, [{"date": "2026-09-01", "item": "Dinner", "type": EXPENSE, "amount": 20}])
    assert first != second


def test_same_receipt_identical_lines_receive_distinct_line_ids():
    root = "abcdef1234567890"
    rows = add_line_ids([
        {"item": "Coke", "amount": 3},
        {"item": "Coke", "amount": 3},
    ], root)
    assert rows[0]["receipt_id"] != rows[1]["receipt_id"]
    assert receipt_root(rows[0]["receipt_id"]) == root
    assert receipt_root(rows[1]["receipt_id"]) == root


def test_whole_receipt_detection_accepts_old_and_line_level_ids():
    root = "abcdef1234567890"
    assert receipt_already_exists(root, [root])
    assert receipt_already_exists(root, [root + "-001", root + "-002"])
    assert not receipt_already_exists(root, ["anotherreceipt01-001"])


def test_final_duplicate_change_never_partially_saves():
    key_a = ("2026-09-01", "a", EXPENSE, 10.0, "root-001")
    key_b = ("2026-09-01", "b", EXPENSE, 20.0, "root-002")
    a = ReceiptCandidate(0, {"date": "2026-09-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 10, "note": "", "receipt_id": "root-001"}, key_a, False, False, "可保存")
    b = ReceiptCandidate(1, {"date": "2026-09-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20, "note": "", "receipt_id": "root-002"}, key_b, False, False, "可保存")
    rows, skipped = finalize_receipt_candidates([a, b], {key_b})
    assert skipped == 1
    assert rows == []


def test_refund_marker_roundtrips_as_refund(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    logical = db.normalize_transaction({"date": "2026-09-01", "item": "Return", "category": "购物", "type": REFUND, "amount": 50, "note": "ok", "receipt_id": "r123456"})
    physical = db._encode_transaction_for_db(logical)
    assert physical["type"] == INCOME
    loaded, issues = db._normalize_loaded_row({"id": 2, **physical})
    assert not issues
    assert loaded["type"] == REFUND
    assert loaded["amount"] == pytest.approx(50)


def test_future_transaction_is_rejected(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-09-02", "item": "Future", "category": "其他", "type": EXPENSE, "amount": 1})


def test_formula_like_export_text_is_neutralized_even_after_whitespace():
    assert sanitize_spreadsheet_text(" =SUM(A1:A2)").startswith("'")
    assert sanitize_spreadsheet_text("\n@SUM(A1:A2)").startswith("'")
    assert sanitize_spreadsheet_text("normal text") == "normal text"


def test_forecast_uses_median_and_preserves_negative_net(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    rows = [{"date": "2026-09-01", "item": "Fixed", "category": "居住", "type": EXPENSE, "amount": 1000}]
    for month, amount in zip([3, 4, 5, 6, 7, 8], [700, 750, 720, 680, 730, 5000]):
        rows.append({"date": f"2026-{month:02d}-15", "item": "Variable", "category": "其他", "type": EXPENSE, "amount": amount})
    result = analytics.historical_month_end_forecast(_frame(rows), 2026, 9, 1)
    assert result["forecast"] < 2000
    negative = analytics.historical_month_end_forecast(
        _frame(rows + [{"date": "2026-09-01", "item": "Refund", "category": "居住", "type": REFUND, "amount": 3000}]),
        2026, 9, 1,
    )
    assert negative["forecast"] < 0
