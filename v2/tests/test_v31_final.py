from __future__ import annotations

import runpy
from datetime import date

import pandas as pd
import pytest
import streamlit as st

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
import wywallet.web as web
from wywallet.ai import FinanceQueryPlan, execute_finance_plan
from wywallet.config import EXPENSE, INCOME, REFUND, REFUND_DB_MARKER
from wywallet.receipt import materialize_receipt_adjustments


def frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "id" not in df:
        df["id"] = range(1, len(df) + 1)
    return df


def test_history_forecast_does_not_multiply_day_one_fixed_costs(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    rows = [
        {"date": "2026-09-01", "item": "Rent", "category": "居住", "type": EXPENSE, "amount": 450},
        {"date": "2026-09-01", "item": "Car", "category": "交通", "type": EXPENSE, "amount": 581},
    ]
    for month in [3, 4, 5, 6, 7, 8]:
        rows += [
            {"date": f"2026-{month:02d}-01", "item": "Fixed", "category": "居住", "type": EXPENSE, "amount": 1000},
            {"date": f"2026-{month:02d}-15", "item": "Variable", "category": "饮食", "type": EXPENSE, "amount": 300},
        ]
    result = analytics.historical_month_end_forecast(frame(rows), 2026, 9, 1)
    assert result["history_months"] == 6
    assert result["forecast"] == pytest.approx(1331)
    assert result["forecast"] < 5000


def test_average_month_is_calendar_month_average(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-01-05", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 80},
        {"date": "2026-08-05", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 80},
    ])
    plan = FinanceQueryPlan(
        intent="amount", subject_mode="specific", subject="Petrol", matched_items=["Petrol"],
        aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-01-01", date_to="2026-08-31",
    )
    assert execute_finance_plan(plan, df)["authoritative_total"] == pytest.approx(20)


def test_custom_comparison_range_is_calculated_locally(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-06-10", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 150},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="specific", subject="Petrol", matched_items=["Petrol"],
        aggregation_mode="specific", aggregation="amount", flow_mode="specific", flow="expense",
        time_mode="specific", date_from="2026-08-01", date_to="2026-08-31",
        comparison="custom", comparison_date_from="2026-06-01", comparison_date_to="2026-06-30",
    )
    result = execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(150)
    assert result["comparison"]["value"] == pytest.approx(100)
    assert result["comparison"]["delta"] == pytest.approx(50)


def test_refund_physical_representation_is_positive_and_marker_based(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    logical = db.normalize_transaction({"date": "2026-08-01", "item": "Return", "category": "购物", "type": REFUND, "amount": 50, "note": "ok"})
    physical = db._encode_transaction_for_db(logical)
    assert physical["type"] == INCOME
    assert physical["amount"] == 50
    assert physical["note"].startswith(REFUND_DB_MARKER)


def test_receipt_adjustments_from_different_receipts_get_distinct_items():
    base = [{"date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 50, "note": ""}]
    first = materialize_receipt_adjustments(base, tax=6, fallback_category="其他", receipt_id="aaaaaaaa1234")
    second = materialize_receipt_adjustments(base, tax=6, fallback_category="其他", receipt_id="bbbbbbbb5678")
    first_tax = [r for r in first if str(r["item"]).startswith("收据税费")][0]
    second_tax = [r for r in second if str(r["item"]).startswith("收据税费")][0]
    assert first_tax["item"] != second_tax["item"]
    assert first_tax["category"] == "其他"


def test_v3_entrypoint_executes_without_extra_dataframe_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(web, "run", lambda: calls.append("run"))
    monkeypatch.setattr(st, "fragment", lambda fn: fn)
    runpy.run_path("v3/app.py", run_name="__main__")
    assert calls == ["run"]
