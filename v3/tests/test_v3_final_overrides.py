from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "v2", ROOT / "v3"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import wywallet.ai as ai
import wywallet.receipt as receipt
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE
from wywallet.receipt import ReceiptCandidate
from v3_overrides import apply_overrides

apply_overrides()


def _frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "id" not in df:
        df["id"] = range(1, len(df) + 1)
    return df


def test_current_year_average_month_excludes_incomplete_current_month(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = _frame([
        {"date": "2026-01-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 800},
        {"date": "2026-08-10", "item": "B", "category": "其他", "type": EXPENSE, "amount": 800},
        {"date": "2026-09-01", "item": "C", "category": "其他", "type": EXPENSE, "amount": 900},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="all",
        aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense",
        time_mode="specific", date_from="2026-01-01", date_to="2026-09-01",
    )
    result = ai.execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(200.0)


def test_max_transaction_returns_identity(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = _frame([
        {"id": 1, "date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 20},
        {"id": 2, "date": "2026-08-03", "item": "Car loan", "category": "交通", "type": EXPENSE, "amount": 581},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="all",
        aggregation_mode="specific", aggregation="max_transaction",
        flow_mode="specific", flow="expense",
        time_mode="specific", date_from="2026-08-01", date_to="2026-08-31",
    )
    result = ai.execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(581)
    assert result["extreme_transaction"]["item"] == "Car loan"
    assert result["extreme_transaction"]["date"] == "2026-08-03"
    summary = ai.authoritative_summary_markdown(result)
    assert "Car loan" in summary


def test_highest_count_uses_transaction_units(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = _frame([
        {"date": "2026-07-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-02", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
    ])
    plan = FinanceQueryPlan(
        intent="trend", subject_mode="specific", subject="Petrol", matched_items=["Petrol"],
        aggregation_mode="specific", aggregation="count",
        flow_mode="specific", flow="expense",
        time_mode="specific", date_from="2026-07-01", date_to="2026-08-31",
        comparison="highest",
    )
    result = ai.execute_finance_plan(plan, df)
    summary = ai.authoritative_summary_markdown(result)
    assert "最高月份：2026-08 · 2 笔" in summary
    assert "RM 2.00" not in summary


def test_comparison_context_is_persisted():
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="all", aggregation="amount", flow="expense",
        time_mode="specific", date_from="2026-08-01", date_to="2026-08-31",
        comparison="custom", comparison_date_from="2026-06-01", comparison_date_to="2026-06-30",
    )
    state = ai.state_from_plan(plan, {"date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert state["comparison"] == "custom"
    assert state["comparison_date_from"] == "2026-06-01"
    assert state["comparison_date_to"] == "2026-06-30"


def test_semantic_receipt_adjustment_id_ignores_photo_hash():
    rows = [{"date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 50, "note": ""}]
    first = receipt.materialize_receipt_adjustments(rows, tax=3, fallback_category="其他", receipt_id="photo-a")
    second = receipt.materialize_receipt_adjustments(rows, tax=3, fallback_category="其他", receipt_id="photo-b")
    first_tax = [row for row in first if str(row["item"]).startswith("收据税费")][0]
    second_tax = [row for row in second if str(row["item"]).startswith("收据税费")][0]
    assert first_tax["item"] == second_tax["item"]


def test_fresh_duplicate_change_blocks_partial_receipt_save():
    candidate_a = ReceiptCandidate(0, {"date": "2026-09-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 10, "note": ""}, ("2026-09-01", "a", EXPENSE, 10.0), False, False, "可保存")
    candidate_b = ReceiptCandidate(1, {"date": "2026-09-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20, "note": ""}, ("2026-09-01", "b", EXPENSE, 20.0), False, False, "可保存")
    rows, skipped = receipt.finalize_receipt_candidates([candidate_a, candidate_b], {candidate_b.key})
    assert skipped == 1
    assert rows == []
