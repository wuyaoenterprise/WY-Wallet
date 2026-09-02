from __future__ import annotations

from datetime import date

import pandas as pd

import wywallet.ai as base_ai
import wywallet.ai_release as ai_release
import wywallet.product_logic as product_logic
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE


def test_current_month_remains_partial_on_its_last_calendar_day(monkeypatch):
    today = date(2026, 9, 30)
    monkeypatch.setattr(product_logic, "today_my", lambda: today)
    monkeypatch.setattr(ai_release, "today_my", lambda: today)
    monkeypatch.setattr(base_ai, "today_my", lambda: today)
    frame = pd.DataFrame([
        {"id": 1, "date": pd.Timestamp("2026-08-15"), "item": "August", "category": "其他", "type": EXPENSE, "amount": 100.0, "note": "", "receipt_id": ""},
        {"id": 2, "date": pd.Timestamp("2026-09-30"), "item": "September", "category": "其他", "type": EXPENSE, "amount": 1.0, "note": "", "receipt_id": ""},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-08-01", date_to="2026-09-30", comparison="lowest",
    )
    result = ai_release.execute_finance_plan(plan, frame)
    assert result["monthly"][-1]["label"] == "2026-09*"
    assert result["lowest_month"]["label"] == "2026-08"
