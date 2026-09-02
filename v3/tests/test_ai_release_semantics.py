from __future__ import annotations

from datetime import date

import pandas as pd

from wywallet import ai_release
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
    frame = _frame([
        {"date": "2026-01-10", "item": "Jan", "category": "其他", "type": "Expense", "amount": 800.0},
        {"date": "2026-09-30", "item": "Sep", "category": "其他", "type": "Expense", "amount": 9000.0},
    ])
    plan = FinanceQueryPlan(
        subject_mode="all", aggregation_mode="specific", aggregation="average_month",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-01-01", date_to="2026-09-30",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["authoritative_total"] == 100.0
