from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import wywallet.ai as base_ai
import wywallet.ai_release as ai_release
import wywallet.analytics as analytics
import wywallet.coverage as coverage
import wywallet.product_logic as product_logic
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE
from wywallet.transaction_commands import _response_matches_payload


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    if "id" not in frame:
        frame["id"] = range(1, len(frame) + 1)
    if "note" not in frame:
        frame["note"] = ""
    if "receipt_id" not in frame:
        frame["receipt_id"] = ""
    return frame


def _freeze(monkeypatch, today: date) -> None:
    monkeypatch.setattr(product_logic, "today_my", lambda: today)
    monkeypatch.setattr(coverage, "today_my", lambda: today)
    monkeypatch.setattr(analytics, "today_my", lambda: today)
    monkeypatch.setattr(ai_release, "today_my", lambda: today)
    monkeypatch.setattr(base_ai, "today_my", lambda: today)


def test_dashboard_percentage_is_suppressed_for_nonpositive_baseline():
    assert coverage.safe_change_ratio(100.0, 0.0) is None
    assert coverage.safe_change_ratio(100.0, -20.0) is None
    assert coverage.safe_change_ratio(120.0, 100.0) == pytest.approx(0.2)


def test_first_tracking_year_view_does_not_invent_pretracking_zero_months(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2023-05-31", "item": "Start", "category": "其他", "type": EXPENSE, "amount": 50.0},
        {"date": "2023-06-15", "item": "June", "category": "其他", "type": EXPENSE, "amount": 100.0},
    ])
    annual = analytics.monthly_summary(transactions, 2023)
    view = coverage.tracked_annual_view(annual, 2023, transactions)
    assert view["month"].tolist()[0] == 5
    assert set(view["month"]).isdisjoint({1, 2, 3, 4})
    assert bool(view.loc[view["month"] == 5, "partial_tracking"].iloc[0]) is True


def test_yoy_aligns_to_common_coverage_when_prior_year_started_late(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2023-05-31", "item": "A", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2023-06-15", "item": "B", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2024-05-31", "item": "A", "category": "其他", "type": EXPENSE, "amount": 200.0},
        {"date": "2024-06-15", "item": "B", "category": "其他", "type": EXPENSE, "amount": 200.0},
    ])
    yoy = coverage.same_period_yoy(transactions, 2024)
    assert yoy is not None
    assert yoy["coverage_aligned"] is True
    assert yoy["previous_start"] == date(2023, 5, 31)
    assert yoy["current_start"] == date(2024, 5, 31)
    assert yoy["previous_total"] == pytest.approx(200.0)
    assert yoy["current_total"] == pytest.approx(400.0)
    assert yoy["change"] == pytest.approx(1.0)


def test_forecast_includes_real_zero_full_month_but_skips_partial_first_month(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2026-07-01", "item": "Tracking start", "category": "其他", "type": EXPENSE, "amount": 1.0},
        {"date": "2026-07-15", "item": "Later July", "category": "其他", "type": EXPENSE, "amount": 100.0},
        # August is a fully tracked genuine zero-spend month.
        {"date": "2026-09-01", "item": "September", "category": "其他", "type": EXPENSE, "amount": 10.0},
    ])
    result = coverage.historical_month_end_forecast(transactions, 2026, 9, 2, lookback=2)
    assert result["history_months"] == 2
    assert result["forecast"] == pytest.approx(60.0)


def test_ai_daily_average_starts_at_real_tracking_date(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2023-05-31", "item": "Start", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2023-06-01", "item": "Next", "category": "其他", "type": EXPENSE, "amount": 100.0},
    ])
    plan = FinanceQueryPlan(
        subject_mode="all", aggregation_mode="specific", aggregation="average_day",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2023-01-01", date_to="2023-12-31",
    )
    result = ai_release.execute_finance_plan(plan, transactions)
    days = (date(2023, 12, 31) - date(2023, 5, 31)).days + 1
    assert result["authoritative_total"] == pytest.approx(round(200.0 / days, 2))
    assert "2023-05-31" in result["coverage_note"]


def test_ai_month_extrema_ignore_untracked_and_current_partial_months(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2026-05-31", "item": "Partial start", "category": "其他", "type": EXPENSE, "amount": 900.0},
        {"date": "2026-06-15", "item": "June", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2026-07-15", "item": "July", "category": "其他", "type": EXPENSE, "amount": 200.0},
        {"date": "2026-08-15", "item": "August", "category": "其他", "type": EXPENSE, "amount": 300.0},
        {"date": "2026-09-01", "item": "Current partial", "category": "其他", "type": EXPENSE, "amount": 1.0},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2026-01-01", date_to="2026-09-30", comparison="lowest",
    )
    result = ai_release.execute_finance_plan(plan, transactions)
    labels = [row["label"] for row in result["monthly"]]
    assert labels[0] == "2026-05†"
    assert labels[-1] == "2026-09*"
    assert result["lowest_month"]["label"] == "2026-06"


def test_ai_suppresses_comparison_that_uses_pretracking_dates(monkeypatch):
    _freeze(monkeypatch, date(2026, 9, 2))
    transactions = _frame([
        {"date": "2023-05-31", "item": "Start", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2024-06-15", "item": "Current", "category": "其他", "type": EXPENSE, "amount": 200.0},
    ])
    plan = FinanceQueryPlan(
        intent="compare", subject_mode="all", aggregation_mode="specific", aggregation="amount",
        flow_mode="specific", flow="expense", time_mode="specific",
        date_from="2024-01-01", date_to="2024-12-31", comparison="previous_year",
    )
    result = ai_release.execute_finance_plan(plan, transactions)
    assert result["comparison"] is None
    assert "未追踪日期" in result["comparison_unavailable_reason"]
    summary = ai_release.authoritative_summary_markdown(result)
    assert "对比未计算" in summary


def test_idempotent_replay_is_only_accepted_when_returned_row_matches_current_payload():
    payload = {
        "date": "2026-09-02", "item": "Lunch", "category": "饮食", "type": "Expense",
        "amount": 12.5, "note": "ok", "receipt_id": None, "flow_subtype": None,
    }
    same = {"id": 10, **payload, "client_token": "token"}
    changed = {**same, "amount": 15.0}
    assert _response_matches_payload(same, payload) is True
    assert _response_matches_payload(changed, payload) is False


def test_active_pages_use_tracking_coverage_and_repair_receipt_detach():
    root = Path(__file__).resolve().parents[2]
    reports = (root / "v3" / "wywallet" / "reports_page.py").read_text(encoding="utf-8")
    dashboard = (root / "v3" / "wywallet" / "dashboard_page.py").read_text(encoding="utf-8")
    settings = (root / "v3" / "wywallet" / "settings_page.py").read_text(encoding="utf-8")
    assert "tracked_annual_view" in reports
    assert "prior_year_has_partial_coverage" in reports
    assert "historical_month_end_forecast" in dashboard
    assert "safe_change_ratio" in dashboard
    assert "detach_receipt_if_identity_changed" in settings
    assert "receipt_identity_changed" in settings
