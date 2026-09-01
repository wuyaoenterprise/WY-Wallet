from __future__ import annotations

import pandas as pd
import pytest

from wywallet.ai import FinanceQueryPlan, execute_finance_plan
from wywallet.analytics import annual_savings_rate, average_monthly_expense, literal_search, monthly_summary, recurring_items, weekday_average
from wywallet.config import EXPENSE, TIMEZONE_NAME
from wywallet.db import normalize_transaction


def frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df: df["note"] = ""
    if "id" not in df: df["id"] = range(1, len(df) + 1)
    return df


def test_timezone_is_malaysia():
    assert TIMEZONE_NAME == "Asia/Kuala_Lumpur"


def test_validation_rejects_invalid_money_and_type():
    with pytest.raises(ValueError):
        normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": "Whatever", "amount": 10})
    with pytest.raises(ValueError):
        normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 0})
    valid = normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 10.129})
    assert valid["amount"] == 10.13


def test_literal_search_does_not_treat_regex_as_regex():
    df = frame([
        {"date": "2026-01-01", "item": "KFC (KL)", "category": "饮食", "type": EXPENSE, "amount": 10, "note": ""},
        {"date": "2026-01-02", "item": "Other", "category": "购物", "type": EXPENSE, "amount": 20, "note": "literal [ bracket"},
    ])
    assert len(literal_search(df, "KFC (KL)")) == 1
    assert len(literal_search(df, "[")) == 1
    assert len(literal_search(df, "*")) == 0


def test_monthly_average_includes_zero_months_for_closed_year():
    df = frame([
        {"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 1200},
        {"date": "2025-02-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 1200},
    ])
    annual = monthly_summary(df, 2025)
    assert average_monthly_expense(annual, 2025) == pytest.approx(200.0)


def test_savings_rate_is_na_without_income():
    df = frame([{"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100}])
    assert annual_savings_rate(monthly_summary(df, 2025)) is None


def test_weekday_chart_uses_average_occurrence_not_raw_total():
    df = frame([
        {"date": "2026-08-03", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
    ])
    result = weekday_average(df, 2026, 8)
    monday = result[result["weekday"] == 0].iloc[0]
    assert monday["平均每个该星期"] == pytest.approx(40.0)


def test_recurring_excludes_frequent_daily_purchase_but_keeps_monthly_subscription():
    rows = []
    for day in [1, 3, 5, 7, 9, 11, 13, 15]:
        rows.append({"date": f"2026-01-{day:02d}", "item": "午餐", "category": "饮食", "type": EXPENSE, "amount": 12})
    for month in [1, 2, 3, 4]:
        rows.append({"date": f"2026-{month:02d}-05", "item": "Netflix", "category": "娱乐", "type": EXPENSE, "amount": 55})
    result = recurring_items(frame(rows))
    assert "Netflix" in result["项目"].tolist()
    assert "午餐" not in result["项目"].tolist()


def test_local_finance_plan_calculates_monthly_fuel_exactly():
    df = frame([
        {"date": "2026-01-02", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 75},
        {"date": "2026-02-02", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 93.8},
        {"date": "2026-02-10", "item": "午餐", "category": "饮食", "type": EXPENSE, "amount": 20},
    ])
    plan = FinanceQueryPlan(intent="trend", subject_mode="specific", subject="打油", metric="expense", year=2026, start_month=1, end_month=2, matched_items=["打油"])
    result = execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(168.8)
    assert [row["value"] for row in result["monthly"]] == [75.0, 93.8]
