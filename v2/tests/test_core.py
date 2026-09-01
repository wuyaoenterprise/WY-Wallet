from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
from wywallet.ai import FinanceQueryPlan, execute_finance_plan
from wywallet.config import EXPENSE, INCOME, TIMEZONE_NAME
from wywallet.receipt import ReceiptCandidate, evaluate_receipt_candidates, finalize_receipt_candidates, reconcile_receipt_total


def frame(rows):
    df = pd.DataFrame(rows)
    if "date" in df:
        df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "id" not in df:
        df["id"] = range(1, len(df) + 1)
    return df


def test_timezone_is_malaysia():
    assert TIMEZONE_NAME == "Asia/Kuala_Lumpur"


def test_validation_rejects_invalid_money_and_type():
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": "Whatever", "amount": 10})
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 0})
    valid = db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 10.129})
    assert valid["amount"] == 10.13


def test_invalid_db_type_is_not_silently_converted_to_expense():
    valid, invalid = db.split_transaction_rows([
        {"id": 1, "date": "2026-08-01", "item": "A", "category": "其他", "type": "Broken", "amount": 10, "note": ""},
        {"id": 2, "date": "2026-08-02", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20, "note": ""},
    ])
    assert valid["id"].tolist() == [2]
    assert len(invalid) == 1
    assert "类型无效" in invalid.iloc[0]["issues"]


def test_literal_search_does_not_treat_regex_as_regex():
    df = frame([
        {"date": "2026-01-01", "item": "KFC (KL)", "category": "饮食", "type": EXPENSE, "amount": 10, "note": ""},
        {"date": "2026-01-02", "item": "Other", "category": "购物", "type": EXPENSE, "amount": 20, "note": "literal [ bracket"},
    ])
    assert len(analytics.literal_search(df, "KFC (KL)")) == 1
    assert len(analytics.literal_search(df, "[")) == 1
    assert len(analytics.literal_search(df, "*")) == 0


def test_monthly_average_includes_zero_months_for_closed_year():
    df = frame([
        {"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 1200},
        {"date": "2025-02-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 1200},
    ])
    assert analytics.average_monthly_expense(analytics.monthly_summary(df, 2025), 2025) == pytest.approx(200.0)


def test_savings_rate_is_na_without_income():
    df = frame([{"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100}])
    assert analytics.annual_savings_rate(analytics.monthly_summary(df, 2025)) is None


def test_yoy_uses_same_date_range_for_current_year(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
        {"date": "2025-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 50},
        {"date": "2025-12-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 1000},
    ])
    result = analytics.same_period_yoy(df, 2026)
    assert result["current_end"] == date(2026, 9, 1)
    assert result["previous_end"] == date(2025, 9, 1)
    assert result["previous_total"] == pytest.approx(50)
    assert result["change"] == pytest.approx(1.0)


def test_weekday_chart_uses_average_occurrence_not_raw_total():
    df = frame([
        {"date": "2026-08-03", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100},
    ])
    monday = analytics.weekday_average(df, 2026, 8).query("weekday == 0").iloc[0]
    assert monday["平均每个该星期"] == pytest.approx(40.0)


def test_recurring_excludes_frequent_daily_purchase_but_keeps_monthly_subscription():
    rows = [{"date": f"2026-01-{day:02d}", "item": "午餐", "category": "饮食", "type": EXPENSE, "amount": 12} for day in [1,3,5,7,9,11,13,15]]
    rows += [{"date": f"2026-{month:02d}-05", "item": "Netflix", "category": "娱乐", "type": EXPENSE, "amount": 55} for month in [1,2,3,4]]
    result = analytics.recurring_items(frame(rows))
    assert "Netflix" in result["项目"].tolist()
    assert "午餐" not in result["项目"].tolist()


def test_local_finance_plan_calculates_monthly_fuel_exactly():
    df = frame([
        {"date": "2026-01-02", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 75},
        {"date": "2026-02-02", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 93.8},
        {"date": "2026-02-10", "item": "午餐", "category": "饮食", "type": EXPENSE, "amount": 20},
    ])
    plan = FinanceQueryPlan(intent="trend", subject_mode="specific", subject="打油", metric_mode="specific", metric="expense", time_mode="specific", date_from="2026-01-01", date_to="2026-02-28", matched_items=["打油"])
    result = execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(168.8)
    assert [row["value"] for row in result["monthly"]] == [75.0, 93.8]


def test_previous_period_comparison_is_calculated_locally():
    df = frame([
        {"date": "2026-07-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 150},
    ])
    plan = FinanceQueryPlan(intent="compare", subject_mode="specific", subject="打油", metric_mode="specific", metric="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31", matched_items=["打油"], comparison="previous_period")
    result = execute_finance_plan(plan, df)
    assert result["comparison"]["date_from"] == "2026-07-01"
    assert result["comparison"]["value"] == pytest.approx(100)
    assert result["comparison"]["delta"] == pytest.approx(50)
    assert result["comparison"]["percent"] == pytest.approx(50)


def test_previous_year_comparison_is_calculated_locally():
    df = frame([
        {"date": "2025-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 80},
        {"date": "2026-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 120},
    ])
    plan = FinanceQueryPlan(intent="compare", subject_mode="specific", subject="打油", metric_mode="specific", metric="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31", matched_items=["打油"], comparison="previous_year")
    result = execute_finance_plan(plan, df)
    assert result["comparison"]["value"] == pytest.approx(80)
    assert result["comparison"]["date_from"] == "2025-08-01"


def test_metric_and_time_inherit_are_application_enforced(monkeypatch):
    proposed = FinanceQueryPlan(intent="amount", subject_mode="inherit", metric_mode="inherit", time_mode="inherit", year_override=2025)
    monkeypatch.setattr(ai, "_generate_content_with_retry", lambda **kwargs: SimpleNamespace(parsed=proposed, text=""))
    df = frame([{"date": "2026-08-01", "item": "Salary", "category": "收入", "type": INCOME, "amount": 1000}])
    state = {"subject": None, "matched_items": [], "matched_categories": [], "metric": "income", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    plan = ai.plan_finance_question("那2025呢？", 2026, df, state, [])
    assert plan.metric == "income"
    assert plan.date_from == "2025-08-01"
    assert plan.date_to == "2025-08-31"


def test_comparison_target_does_not_replace_prior_primary_range(monkeypatch):
    # Simulate a model trying to make July the new primary range for
    # "跟上个月比". Application logic must keep August primary and compare July.
    proposed = FinanceQueryPlan(
        intent="compare", subject_mode="inherit", metric_mode="inherit", time_mode="specific",
        date_from="2026-07-01", date_to="2026-07-31", comparison="previous_period",
    )
    monkeypatch.setattr(ai, "_generate_content_with_retry", lambda **kwargs: SimpleNamespace(parsed=proposed, text=""))
    df = frame([
        {"date": "2026-07-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 150},
    ])
    state = {"subject": "打油", "matched_items": ["打油"], "matched_categories": [], "metric": "expense", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    plan = ai.plan_finance_question("跟上个月比呢？", 2026, df, state, [])
    assert plan.date_from == "2026-08-01"
    assert plan.date_to == "2026-08-31"
    result = execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(150)
    assert result["comparison"]["value"] == pytest.approx(100)


def test_list_result_is_not_limited_to_120_rows():
    rows = [{"date": f"2026-08-{(i%28)+1:02d}", "item": "Grab", "category": "交通", "type": EXPENSE, "amount": 10+i/100, "id": i+1} for i in range(180)]
    plan = FinanceQueryPlan(intent="list", subject_mode="specific", subject="Grab", metric_mode="specific", metric="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31", matched_items=["Grab"])
    result = execute_finance_plan(plan, frame(rows))
    assert result["transaction_count"] == 180
    assert len(result["ui_transactions"]) == 180
    assert len(result["preview_transactions"]) == 20
    compact = ai._compact_result_for_ai(result)
    assert "ui_transactions" not in compact
    assert compact["complete_list_rendered_locally"] is True


def test_ledger_signature_changes_when_text_changes():
    first = frame([{"id": 1, "date": "2026-08-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 50, "note": ""}])
    second = first.copy()
    second.loc[0, "item"] = "打油"
    assert db.ledger_signature(first) != db.ledger_signature(second)


def test_default_categories_do_not_reappear_after_registered_categories_exist(monkeypatch):
    monkeypatch.setattr(db, "load_category_rows", lambda: ["饮食", "日常消费"])
    df = frame([{"date": "2026-08-01", "item": "A", "category": "饮食", "type": EXPENSE, "amount": 10}])
    result = db.load_categories(df)
    assert "购物" not in result
    assert "日常消费" in result


def test_default_categories_still_bootstrap_when_category_table_is_empty(monkeypatch):
    monkeypatch.setattr(db, "load_category_rows", lambda: [])
    df = frame([{"date": "2026-08-01", "item": "Vet", "category": "宠物", "type": EXPENSE, "amount": 50}])
    result = db.load_categories(df)
    assert "饮食" in result
    assert "其他" in result
    assert "宠物" in result


def test_receipt_duplicate_can_be_explicitly_forced():
    base = {"date": date(2026,8,1), "item": "Coke", "category": "饮食", "type": EXPENSE, "amount": 3.0, "note": ""}
    key = db.transaction_key(base)
    edited = pd.DataFrame([{**base, "保存": True, "日期已确认": True, "仍然保存重复": True}])
    statuses, candidates = evaluate_receipt_candidates(edited, {key})
    assert statuses == ["重复但已确认"]
    assert len(candidates) == 1
    final, skipped = finalize_receipt_candidates(candidates, {key})
    assert len(final) == 1
    assert skipped == 0


def test_receipt_missing_date_requires_confirmation():
    edited = pd.DataFrame([{"保存": True, "日期已确认": False, "仍然保存重复": False, "date": date(2026,9,1), "item": "A", "category": "其他", "type": EXPENSE, "amount": 10, "note": ""}])
    statuses, candidates = evaluate_receipt_candidates(edited, set())
    assert statuses == ["需确认日期"]
    assert candidates == []


def test_receipt_total_reconciliation_detects_difference():
    normalized = db.normalize_transaction({"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 90})
    candidate = ReceiptCandidate(0, normalized, db.transaction_key(normalized), False, False, "可保存")
    result = reconcile_receipt_total([candidate], 100)
    assert result["matches"] is False
    assert result["difference"] == pytest.approx(-10)


def test_retry_retries_transient_errors(monkeypatch):
    calls = {"n": 0}
    class Models:
        def generate_content(self, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("503 unavailable")
            return "ok"
    monkeypatch.setattr(ai, "get_ai_client", lambda: SimpleNamespace(models=Models()))
    monkeypatch.setattr(ai.time, "sleep", lambda *_: None)
    assert ai._generate_content_with_retry(model="x", contents="y") == "ok"
    assert calls["n"] == 3
