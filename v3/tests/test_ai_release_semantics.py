from __future__ import annotations

from datetime import date

import pandas as pd

from wywallet import ai as base_ai
from wywallet import ai_release, product_logic
from wywallet.ai import FinanceQueryPlan


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    if "id" not in frame:
        frame["id"] = range(1, len(frame) + 1)
    if "note" not in frame:
        frame["note"] = ""
    return frame


def test_english_subject_matching_uses_word_boundaries():
    frame = _frame([
        {"date": "2026-01-01", "item": "Digi Postpaid", "category": "电话费", "type": "Expense", "amount": 50.0},
        {"date": "2026-01-02", "item": "Digital Service", "category": "软件", "type": "Expense", "amount": 80.0},
    ])
    items, _ = ai_release._local_subject_matches("digi", frame)
    assert "Digi Postpaid" in items
    assert "Digital Service" not in items


def test_food_subject_includes_all_food_related_ledger_categories():
    frame = _frame([
        {"date": "2026-01-01", "item": "杂饭", "category": "午餐", "type": "Expense", "amount": 10.0},
        {"date": "2026-01-02", "item": "排骨", "category": "肉类", "type": "Expense", "amount": 20.0},
        {"date": "2026-01-03", "item": "青菜", "category": "蔬菜", "type": "Expense", "amount": 5.0},
        {"date": "2026-01-04", "item": "奶茶", "category": "饮料", "type": "Expense", "amount": 6.0},
        {"date": "2026-01-05", "item": "Car loan", "category": "车贷", "type": "Expense", "amount": 581.0},
    ])
    _, categories = ai_release._local_subject_matches("所有和吃相关的餐饮", frame)
    assert {"午餐", "肉类", "蔬菜", "饮料"}.issubset(set(categories))
    assert "车贷" not in categories


def test_multi_subject_monthly_answer_splits_fuel_and_food_without_gemini(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 14))
    frame = _frame([
        {"date": "2026-01-03", "item": "加油", "category": "打油", "type": "Expense", "amount": 50.0},
        {"date": "2026-01-04", "item": "杂饭", "category": "午餐", "type": "Expense", "amount": 30.0},
        {"date": "2026-02-03", "item": "车油", "category": "打油", "type": "Expense", "amount": 40.0},
        {"date": "2026-02-04", "item": "苹果", "category": "水果", "type": "Expense", "amount": 20.0},
        {"date": "2026-02-05", "item": "房租", "category": "租金", "type": "Expense", "amount": 450.0},
    ])
    direct = ai_release.try_local_finance_answer(
        "你统计一下我今年每个月的加油数据和餐饮开销数据（所有和吃相关的）",
        2026,
        frame,
        {},
    )
    assert direct is not None
    text = direct["markdown"]
    assert "| 月份 | 加油净支出 | 餐饮净支出 |" in text
    assert "2026-01 | RM 50.00 | RM 30.00" in text
    assert "2026-02 | RM 40.00 | RM 20.00" in text
    assert "RM 450.00" not in text
    assert direct["state"]["multi_subjects"] == ["加油", "餐饮"]

    followup = ai_release.try_local_finance_answer("我要看每月分布", 2026, frame, direct["state"])
    assert followup is not None
    assert "2026-02 | RM 40.00 | RM 20.00" in followup["markdown"]


def test_current_month_previous_month_question_is_planned_locally(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 14))
    monkeypatch.setattr(base_ai, "plan_finance_question", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Gemini planner should not run")))
    frame = _frame([
        {"date": "2026-09-01", "item": "Lunch", "category": "午餐", "type": "Expense", "amount": 20.0},
    ])
    plan = ai_release.plan_finance_question("为什么我这个月开销比上个月涨了5%", 2026, frame, {}, [])
    assert plan.intent == "explain"
    assert plan.subject_mode == "all"
    assert plan.date_from == "2026-09-01"
    assert plan.date_to == "2026-09-14"
    assert plan.comparison == "previous_month"


def test_comparison_explanation_uses_period_deltas_not_large_fixed_costs(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 14))
    monkeypatch.setattr(base_ai, "today_my", lambda: date(2026, 9, 14))
    frame = _frame([
        {"date": "2026-08-01", "item": "车贷", "category": "车贷", "type": "Expense", "amount": 581.0},
        {"date": "2026-08-02", "item": "午餐", "category": "午餐", "type": "Expense", "amount": 10.0},
        {"date": "2026-09-01", "item": "车贷", "category": "车贷", "type": "Expense", "amount": 581.0},
        {"date": "2026-09-02", "item": "午餐", "category": "午餐", "type": "Expense", "amount": 30.0},
    ])
    plan = FinanceQueryPlan(
        intent="explain",
        subject_mode="all",
        aggregation_mode="specific",
        aggregation="amount",
        flow_mode="specific",
        flow="expense",
        time_mode="specific",
        date_from="2026-09-01",
        date_to="2026-09-14",
        comparison="previous_month",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["comparison"]["delta"] == 20.0
    drivers = result["comparison_drivers"]["categories"]
    assert any(row["category"] == "午餐" and row["delta"] == 20.0 for row in drivers)
    assert not any(row["category"] == "车贷" for row in drivers)
    explanation = ai_release.answer_finance_question("为什么涨了？上个月也有车贷啊", result)
    assert "真正要看的是两期**差额**" in explanation
    assert "午餐 +20.00" in explanation
    assert "车贷" in explanation and "不应被当成上涨原因" in explanation


def test_nonpositive_comparison_base_has_no_percent(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 8, 31))
    frame = _frame([
        {"date": "2026-07-01", "item": "Purchase", "category": "购物", "type": "Expense", "amount": 50.0},
        {"date": "2026-07-02", "item": "Refund", "category": "购物", "type": "Refund", "amount": 100.0},
        {"date": "2026-08-01", "item": "Purchase", "category": "购物", "type": "Expense", "amount": 100.0},
    ])
    plan = FinanceQueryPlan(
        subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-08-01", date_to="2026-08-31", comparison="previous_month",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["comparison"]["value"] == -50.0
    assert result["comparison"]["percent"] is None


def test_current_month_excluded_from_ytd_monthly_average_even_on_last_day(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 30))
    monkeypatch.setattr(product_logic, "today_my", lambda: date(2026, 9, 30))
    frame = _frame([
        {"date": "2026-01-01", "item": "Jan", "category": "其他", "type": "Expense", "amount": 800.0},
        {"date": "2026-09-30", "item": "Sep", "category": "其他", "type": "Expense", "amount": 9000.0},
    ])
    plan = FinanceQueryPlan(
        subject_mode="all", aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-01-01", date_to="2026-09-30",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["authoritative_total"] == 100.0


def test_first_partial_history_year_average_does_not_count_pretracking_zero_months(monkeypatch):
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 2))
    monkeypatch.setattr(product_logic, "today_my", lambda: date(2026, 9, 2))
    frame = _frame([
        {"date": f"2023-{month:02d}-15", "item": "Tracked", "category": "其他", "type": "Expense", "amount": 100.0}
        for month in range(5, 13)
    ])
    plan = FinanceQueryPlan(
        subject_mode="all", aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2023-01-01", date_to="2023-12-31",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["authoritative_total"] == 100.0
