from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
from wywallet.ai import FinanceQueryPlan, authoritative_summary_markdown, execute_finance_plan, finance_list_frame
from wywallet.config import EXPENSE, INCOME, REFUND, TIMEZONE_NAME
from wywallet.exporting import sanitize_export_frame, sanitize_spreadsheet_text
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


def plan(**kwargs):
    defaults = dict(
        intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-08-01", date_to="2026-08-31",
    )
    defaults.update(kwargs)
    return FinanceQueryPlan(**defaults)


def test_timezone_is_malaysia():
    assert TIMEZONE_NAME == "Asia/Kuala_Lumpur"


def test_validation_rejects_invalid_money_type_and_future(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": "Whatever", "amount": 10})
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 0})
    with pytest.raises(ValueError):
        db.normalize_transaction({"date": "2026-09-02", "item": "Future", "category": "其他", "type": EXPENSE, "amount": 10})
    valid = db.normalize_transaction({"date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": REFUND, "amount": 10.129})
    assert valid["type"] == REFUND and valid["amount"] == 10.13


def test_future_or_invalid_db_rows_are_isolated(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    valid, invalid = db.split_transaction_rows([
        {"id": 1, "date": "2026-09-02", "item": "Future", "category": "其他", "type": EXPENSE, "amount": 10, "note": ""},
        {"id": 2, "date": "2026-08-02", "item": "Broken", "category": "其他", "type": "Wrong", "amount": 20, "note": ""},
        {"id": 3, "date": "2026-08-03", "item": "OK", "category": "其他", "type": REFUND, "amount": 5, "note": ""},
    ])
    assert valid["id"].tolist() == [3]
    assert len(invalid) == 2
    assert "未来日期" in " ".join(invalid["issues"].tolist())
    assert "类型无效" in " ".join(invalid["issues"].tolist())


def test_literal_search_is_not_regex():
    df = frame([
        {"date": "2026-01-01", "item": "KFC (KL)", "category": "饮食", "type": EXPENSE, "amount": 10},
        {"date": "2026-01-02", "item": "Other", "category": "购物", "type": EXPENSE, "amount": 20, "note": "literal [ bracket"},
    ])
    assert len(analytics.literal_search(df, "KFC (KL)")) == 1
    assert len(analytics.literal_search(df, "[")) == 1
    assert len(analytics.literal_search(df, "*")) == 0


def test_refund_reduces_spending_without_inflating_income(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-08-01", "item": "Salary", "category": "收入", "type": INCOME, "amount": 1000},
        {"date": "2026-08-02", "item": "Shoes", "category": "购物", "type": EXPENSE, "amount": 300},
        {"date": "2026-08-03", "item": "Shoes return", "category": "购物", "type": REFUND, "amount": 100},
    ])
    income, expense, balance = analytics.calculate_totals(df)
    assert income == pytest.approx(1000)
    assert expense == pytest.approx(200)
    assert balance == pytest.approx(800)
    august = analytics.monthly_summary(df, 2026).query("month == 8").iloc[0]
    assert august["收入"] == pytest.approx(1000)
    assert august["退款"] == pytest.approx(100)
    assert august["支出"] == pytest.approx(200)


def test_monthly_average_includes_zero_months_for_closed_year(monkeypatch):
    monkeypatch.setattr(analytics, "now_my", lambda: pd.Timestamp("2026-09-01", tz="Asia/Kuala_Lumpur"))
    df = frame([
        {"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 1200},
        {"date": "2025-02-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 1200},
    ])
    assert analytics.average_monthly_expense(analytics.monthly_summary(df, 2025), 2025) == pytest.approx(200.0)


def test_savings_rate_is_na_without_income():
    df = frame([{"date": "2025-01-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100}])
    assert analytics.annual_savings_rate(analytics.monthly_summary(df, 2025)) is None


def test_yoy_uses_same_date_range_and_refund_effect(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 120},
        {"date": "2026-08-02", "item": "A refund", "category": "其他", "type": REFUND, "amount": 20},
        {"date": "2025-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 50},
        {"date": "2025-12-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 1000},
    ])
    result = analytics.same_period_yoy(df, 2026)
    assert result["current_end"] == date(2026, 9, 1)
    assert result["previous_end"] == date(2025, 9, 1)
    assert result["current_total"] == pytest.approx(100)
    assert result["previous_total"] == pytest.approx(50)
    assert result["change"] == pytest.approx(1.0)


def test_weekday_current_month_uses_only_elapsed_weekdays(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    df = frame([{"date": "2026-09-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100}])
    tuesday = analytics.weekday_average(df, 2026, 9).query("weekday == 1").iloc[0]
    assert tuesday["出现次数"] == 1
    assert tuesday["平均每个该星期"] == pytest.approx(100)


def test_dashboard_previous_month_comparison_uses_same_elapsed_days(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 5))
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-05", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20},
        {"date": "2026-08-20", "item": "Late", "category": "其他", "type": EXPENSE, "amount": 1000},
    ])
    previous = analytics.previous_month_same_elapsed_slice(df, 2026, 9, 5)
    assert previous["amount"].sum() == pytest.approx(30)


def test_posted_only_excludes_future_rows(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    df = frame([
        {"date": "2026-09-01", "item": "Now", "category": "其他", "type": EXPENSE, "amount": 10},
        {"date": "2026-09-02", "item": "Future", "category": "其他", "type": EXPENSE, "amount": 1000},
    ])
    assert analytics.calculate_totals(df)[1] == pytest.approx(10)


def test_recurring_excludes_daily_purchase_and_keeps_subscription():
    rows = [{"date": f"2026-01-{day:02d}", "item": "午餐", "category": "饮食", "type": EXPENSE, "amount": 12} for day in [1,3,5,7,9,11,13,15]]
    rows += [{"date": f"2026-{month:02d}-05", "item": "Netflix", "category": "娱乐", "type": EXPENSE, "amount": 55} for month in [1,2,3,4]]
    result = analytics.recurring_items(frame(rows))
    assert "Netflix" in result["项目"].tolist()
    assert "午餐" not in result["项目"].tolist()


def test_ai_expense_amount_nets_refunds():
    df = frame([
        {"date": "2026-08-01", "item": "Shoes", "category": "购物", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-02", "item": "Shoes", "category": "购物", "type": REFUND, "amount": 30},
    ])
    assert execute_finance_plan(plan(), df)["authoritative_total"] == pytest.approx(70)


def test_ai_expense_count_excludes_income_and_refunds():
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-02", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20},
        {"date": "2026-08-03", "item": "R", "category": "其他", "type": REFUND, "amount": 5},
        {"date": "2026-08-04", "item": "Salary", "category": "收入", "type": INCOME, "amount": 100},
    ])
    assert execute_finance_plan(plan(aggregation="count"), df)["authoritative_total"] == pytest.approx(2)


def test_ai_income_does_not_include_refund():
    df = frame([
        {"date": "2026-08-01", "item": "Salary", "category": "收入", "type": INCOME, "amount": 1000},
        {"date": "2026-08-02", "item": "Return", "category": "购物", "type": REFUND, "amount": 200},
    ])
    assert execute_finance_plan(plan(flow="income"), df)["authoritative_total"] == pytest.approx(1000)


def test_semantic_union_includes_all_fuel_synonyms():
    df = frame([
        {"date": "2026-08-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 80},
        {"date": "2026-08-02", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 70},
        {"date": "2026-08-03", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 20},
    ])
    result = execute_finance_plan(plan(subject_mode="specific", subject="油费", matched_items=["打油"]), df)
    assert result["authoritative_total"] == pytest.approx(150)
    assert set(result["matched_items"]) >= {"打油", "Petrol"}


def test_previous_period_comparison_is_calculated_locally():
    df = frame([
        {"date": "2026-07-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 150},
    ])
    result = execute_finance_plan(plan(subject_mode="specific", subject="打油", matched_items=["打油"], comparison="previous_period"), df)
    assert result["comparison"]["date_from"] == "2026-07-01"
    assert result["comparison"]["value"] == pytest.approx(100)


def test_previous_year_comparison_is_calculated_locally():
    df = frame([
        {"date": "2025-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 80},
        {"date": "2026-08-10", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 120},
    ])
    result = execute_finance_plan(plan(subject_mode="specific", subject="打油", matched_items=["打油"], comparison="previous_year"), df)
    assert result["comparison"]["value"] == pytest.approx(80)
    assert result["comparison"]["date_from"] == "2025-08-01"


def test_aggregation_flow_and_time_inherit_are_application_enforced(monkeypatch):
    proposed = FinanceQueryPlan(intent="amount", subject_mode="inherit", aggregation_mode="inherit", flow_mode="inherit", time_mode="inherit", year_override=2025)
    monkeypatch.setattr(ai, "_generate_content_with_retry", lambda **kwargs: SimpleNamespace(parsed=proposed, text=""))
    df = frame([{"date": "2026-08-01", "item": "Salary", "category": "收入", "type": INCOME, "amount": 1000}])
    state = {"subject": None, "matched_items": [], "matched_categories": [], "aggregation": "count", "flow": "income", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    resolved = ai.plan_finance_question("那2025呢？", 2026, df, state, [])
    assert resolved.aggregation == "count" and resolved.flow == "income"
    assert resolved.date_from == "2025-08-01" and resolved.date_to == "2025-08-31"


def test_comparison_target_does_not_replace_prior_primary_range(monkeypatch):
    proposed = FinanceQueryPlan(intent="compare", subject_mode="inherit", aggregation_mode="inherit", flow_mode="inherit", time_mode="specific", date_from="2026-07-01", date_to="2026-07-31", comparison="previous_period")
    monkeypatch.setattr(ai, "_generate_content_with_retry", lambda **kwargs: SimpleNamespace(parsed=proposed, text=""))
    df = frame([
        {"date": "2026-07-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-01", "item": "打油", "category": "交通", "type": EXPENSE, "amount": 150},
    ])
    state = {"subject": "打油", "matched_items": ["打油"], "matched_categories": [], "aggregation": "amount", "flow": "expense", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    resolved = ai.plan_finance_question("跟上个月比呢？", 2026, df, state, [])
    assert resolved.date_from == "2026-08-01" and resolved.date_to == "2026-08-31"
    result = execute_finance_plan(resolved, df)
    assert result["authoritative_total"] == pytest.approx(150)
    assert result["comparison"]["value"] == pytest.approx(100)


def test_lowest_month_includes_zero_months():
    df = frame([{"date": "2026-01-05", "item": "A", "category": "其他", "type": EXPENSE, "amount": 50}])
    result = execute_finance_plan(FinanceQueryPlan(intent="trend", subject_mode="all", aggregation_mode="specific", aggregation="amount", flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-01-01", date_to="2026-02-28", comparison="lowest"), df)
    assert result["lowest_month"]["label"] == "2026-02"
    assert result["lowest_month"]["value"] == pytest.approx(0)


def test_amount_query_does_not_build_raw_list_but_list_frame_is_complete():
    rows = [{"date": f"2026-08-{(i%28)+1:02d}", "item": "Grab", "category": "交通", "type": EXPENSE, "amount": 10+i/100, "id": i+1} for i in range(180)]
    df = frame(rows)
    amount_result = execute_finance_plan(plan(), df)
    assert amount_result["ui_transactions"] == []
    list_plan = plan(intent="list", subject_mode="specific", subject="Grab", matched_items=["Grab"])
    assert len(finance_list_frame(list_plan, df)) == 180
    assert execute_finance_plan(list_plan, df)["ui_transactions"] == []


def test_authoritative_summary_contains_python_result():
    result = execute_finance_plan(plan(), frame([{"date": "2026-08-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 148.75}]))
    text = authoritative_summary_markdown(result)
    assert "RM 148.75" in text and "本地精确结果" in text


def test_ledger_signature_changes_when_text_changes():
    first = frame([{"id": 1, "date": "2026-08-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 50}])
    second = first.copy(); second.loc[0, "item"] = "打油"
    assert db.ledger_signature(first) != db.ledger_signature(second)


def test_category_casing_is_canonicalized():
    df = frame([
        {"date": "2026-08-01", "item": "A", "category": "Travel", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-02", "item": "B", "category": "travel", "type": EXPENSE, "amount": 20},
    ])
    assert db.canonicalize_transaction_categories(df, ["Travel"])["category"].tolist() == ["Travel", "Travel"]


def test_default_categories_lifecycle(monkeypatch):
    monkeypatch.setattr(db, "load_category_rows", lambda: ["饮食", "日常消费"])
    df = frame([{"date": "2026-08-01", "item": "A", "category": "饮食", "type": EXPENSE, "amount": 10}])
    result = db.load_categories(df)
    assert "购物" not in result and "日常消费" in result
    monkeypatch.setattr(db, "load_category_rows", lambda: [])
    result = db.load_categories(frame([{"date": "2026-08-01", "item": "Vet", "category": "宠物", "type": EXPENSE, "amount": 50}]))
    assert "饮食" in result and "其他" in result and "宠物" in result


def test_receipt_duplicate_can_be_forced():
    base = {"date": date(2026,8,1), "item": "Coke", "category": "饮食", "type": EXPENSE, "amount": 3.0, "note": ""}
    key = db.transaction_key(base)
    edited = pd.DataFrame([{**base, "保存": True, "日期已确认": True, "仍然保存重复": True}])
    statuses, candidates = evaluate_receipt_candidates(edited, {key})
    assert statuses == ["重复但已确认"]
    final, skipped = finalize_receipt_candidates(candidates, {key})
    assert len(final) == 1 and skipped == 0


def test_receipt_missing_date_requires_confirmation():
    edited = pd.DataFrame([{"保存": True, "日期已确认": False, "仍然保存重复": False, "date": date(2026,9,1), "item": "A", "category": "其他", "type": EXPENSE, "amount": 10, "note": ""}])
    statuses, candidates = evaluate_receipt_candidates(edited, set())
    assert statuses == ["需确认日期"] and candidates == []


def test_receipt_reconciliation_includes_tax_service_discount_and_refund():
    expense = db.normalize_transaction({"date": "2026-08-01", "item": "Food", "category": "饮食", "type": EXPENSE, "amount": 100})
    exp_candidate = ReceiptCandidate(0, expense, db.transaction_key(expense), False, False, "可保存")
    result = reconcile_receipt_total([exp_candidate], 111, tax=6, service_charge=10, discount=5)
    assert result["expected_total"] == pytest.approx(111) and result["matches"] is True
    refund = db.normalize_transaction({"date": "2026-08-01", "item": "Return", "category": "购物", "type": REFUND, "amount": 100})
    refund_candidate = ReceiptCandidate(0, refund, db.transaction_key(refund), False, False, "可保存")
    result = reconcile_receipt_total([refund_candidate], -100)
    assert result["expected_total"] == pytest.approx(-100) and result["matches"] is True


def test_spreadsheet_formula_injection_is_neutralized():
    assert sanitize_spreadsheet_text("=HYPERLINK('x')").startswith("'=")
    assert sanitize_spreadsheet_text("+cmd").startswith("'+")
    assert sanitize_spreadsheet_text("normal") == "normal"
    safe = sanitize_export_frame(pd.DataFrame({"note": ["@SUM(1,2)", "safe"]}))
    assert safe.iloc[0]["note"].startswith("'@")


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
