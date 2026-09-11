from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import wywallet.ai as ai
from wywallet.ai import FinanceQueryPlan, execute_finance_plan, finance_list_frame
from wywallet.config import EXPENSE, REFUND


def frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "id" not in df:
        df["id"] = range(1, len(df) + 1)
    return df


def test_previous_month_partial_range_keeps_same_day_span():
    df = frame([
        {"date": "2026-07-05", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 40},
        {"date": "2026-07-20", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 900},
        {"date": "2026-08-05", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 60},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="specific", subject="Petrol",
        aggregation_mode="specific", aggregation="amount", flow_mode="specific", flow="expense",
        time_mode="specific", date_from="2026-08-01", date_to="2026-08-15",
        matched_items=["Petrol"], comparison="previous_month",
    )
    result = execute_finance_plan(plan, df)
    assert result["comparison"]["date_from"] == "2026-07-01"
    assert result["comparison"]["date_to"] == "2026-07-15"
    assert result["comparison"]["value"] == pytest.approx(40)


def test_followup_phrase_forces_previous_month_even_if_model_says_previous_period(monkeypatch):
    proposed = FinanceQueryPlan(
        intent="compare", subject_mode="inherit", aggregation_mode="inherit", flow_mode="inherit",
        time_mode="inherit", comparison="previous_period",
    )
    monkeypatch.setattr(ai, "_generate_content_with_retry", lambda **kwargs: SimpleNamespace(parsed=proposed, text=""))
    df = frame([{"date": "2026-08-05", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 60}])
    state = {
        "subject": "Petrol", "matched_items": ["Petrol"], "matched_categories": [],
        "aggregation": "amount", "flow": "expense", "date_from": "2026-08-01", "date_to": "2026-08-15",
    }
    plan = ai.plan_finance_question("跟上个月比", 2026, df, state, [])
    assert plan.comparison == "previous_month"
    assert plan.date_from == "2026-08-01"
    assert plan.date_to == "2026-08-15"


def test_expense_count_and_list_exclude_refunds_but_amount_nets_them():
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "购物", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-02", "item": "A", "category": "购物", "type": REFUND, "amount": 30},
    ])
    amount_plan = FinanceQueryPlan(
        intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-08-01", date_to="2026-08-31",
    )
    count_plan = amount_plan.model_copy(update={"intent": "amount", "aggregation": "count"})
    list_plan = amount_plan.model_copy(update={"intent": "list"})

    assert execute_finance_plan(amount_plan, df)["authoritative_total"] == pytest.approx(70)
    count_result = execute_finance_plan(count_plan, df)
    assert count_result["authoritative_total"] == pytest.approx(1)
    assert count_result["transaction_count"] == 1
    listed = finance_list_frame(list_plan, df)
    assert listed["type"].tolist() == [EXPENSE]
