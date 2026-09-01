from __future__ import annotations

import runpy
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
V3_ROOT = ROOT / "v3"
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
import wywallet.receipt as receipt
import wywallet.web as web
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE, INCOME, REFUND, RECEIPT_META_PREFIX, REFUND_DB_MARKER
from wywallet.receipt import ReceiptCandidate


def _frame(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    if "note" not in df:
        df["note"] = ""
    if "receipt_id" not in df:
        df["receipt_id"] = ""
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
    plan = FinanceQueryPlan(intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="average_month", flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-01-01", date_to="2026-09-01")
    assert ai.execute_finance_plan(plan, df)["authoritative_total"] == pytest.approx(200.0)


def test_max_transaction_returns_identity(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = _frame([
        {"id": 1, "date": "2026-08-01", "item": "Lunch", "category": "饮食", "type": EXPENSE, "amount": 20},
        {"id": 2, "date": "2026-08-03", "item": "Car loan", "category": "交通", "type": EXPENSE, "amount": 581},
    ])
    plan = FinanceQueryPlan(intent="amount", subject_mode="all", aggregation_mode="specific", aggregation="max_transaction", flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31")
    result = ai.execute_finance_plan(plan, df)
    assert result["authoritative_total"] == pytest.approx(581)
    assert result["extreme_transaction"]["item"] == "Car loan"
    assert result["extreme_transaction"]["date"] == "2026-08-03"
    assert "Car loan" in ai.authoritative_summary_markdown(result)


def test_highest_count_uses_transaction_units(monkeypatch):
    monkeypatch.setattr(ai, "today_my", lambda: date(2026, 9, 1))
    df = _frame([
        {"date": "2026-07-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-01", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
        {"date": "2026-08-02", "item": "Petrol", "category": "交通", "type": EXPENSE, "amount": 10},
    ])
    plan = FinanceQueryPlan(intent="trend", subject_mode="specific", subject="Petrol", matched_items=["Petrol"], aggregation_mode="specific", aggregation="count", flow_mode="specific", flow="expense", time_mode="specific", date_from="2026-07-01", date_to="2026-08-31", comparison="highest")
    summary = ai.authoritative_summary_markdown(ai.execute_finance_plan(plan, df))
    assert "最高月份：2026-08 · 2 笔" in summary
    assert "RM 2.00" not in summary


def test_comparison_context_is_persisted():
    plan = FinanceQueryPlan(intent="compare", subject_mode="all", aggregation="amount", flow="expense", time_mode="specific", date_from="2026-08-01", date_to="2026-08-31", comparison="custom", comparison_date_from="2026-06-01", comparison_date_to="2026-06-30")
    state = ai.state_from_plan(plan, {"date_from": "2026-08-01", "date_to": "2026-08-31"})
    assert state["comparison"] == "custom"
    assert state["comparison_date_from"] == "2026-06-01"
    assert state["comparison_date_to"] == "2026-06-30"


def test_local_semantics_match_malaysia_merchants():
    df = _frame([
        {"date": "2026-08-01", "item": "TNB", "category": "居住", "type": EXPENSE, "amount": 100},
        {"date": "2026-08-02", "item": "Unifi Home", "category": "居住", "type": EXPENSE, "amount": 120},
        {"date": "2026-08-03", "item": "Prudential", "category": "保险", "type": EXPENSE, "amount": 200},
    ])
    assert "TNB" in ai._fallback_subject_matches("电费", df)[0]
    assert "Unifi Home" in ai._fallback_subject_matches("网费", df)[0]
    assert "Prudential" in ai._fallback_subject_matches("保险", df)[0]


def test_semantic_receipt_id_prefers_receipt_number_and_is_photo_independent():
    payload = {"merchant": "ABC Cafe", "receipt_number": "R-123", "receipt_total": 53.0}
    rows = [{"date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 50, "note": ""}]
    first = receipt.semantic_receipt_id(payload, rows)
    second = receipt.semantic_receipt_id(dict(payload), list(rows))
    assert first == second
    assert len(first) == 16


def test_receipt_adjustments_keep_clean_item_names_and_hidden_identity():
    rows = [{"date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 50, "note": ""}]
    result = receipt.materialize_receipt_adjustments(rows, tax=3, fallback_category="其他", receipt_id="receipt123")
    tax = [row for row in result if row["item"] == "收据税费"][0]
    assert tax["receipt_id"] == "receipt123"
    assert "receipt123" not in tax["item"]


def test_receipt_metadata_roundtrips_without_visible_note_pollution(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    logical = db.normalize_transaction({"date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 50, "note": "user note", "receipt_id": "abc123456"})
    physical = db._encode_transaction_for_db(logical)
    assert RECEIPT_META_PREFIX in physical["note"]
    loaded, issues = db._normalize_loaded_row({"id": 1, **physical})
    assert not issues
    assert loaded["receipt_id"] == "abc123456"
    assert loaded["note"] == "user note"


def test_refund_marker_and_receipt_metadata_can_coexist(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    logical = db.normalize_transaction({"date": "2026-09-01", "item": "Return", "category": "购物", "type": REFUND, "amount": 50, "note": "ok", "receipt_id": "r123456"})
    physical = db._encode_transaction_for_db(logical)
    assert physical["type"] == INCOME
    assert physical["note"].startswith(REFUND_DB_MARKER)
    loaded, issues = db._normalize_loaded_row({"id": 2, **physical})
    assert not issues
    assert loaded["type"] == REFUND
    assert loaded["receipt_id"] == "r123456"


def test_fresh_duplicate_change_blocks_partial_receipt_save():
    key_a = ("2026-09-01", "a", EXPENSE, 10.0, "r1")
    key_b = ("2026-09-01", "b", EXPENSE, 20.0, "r1")
    candidate_a = ReceiptCandidate(0, {"date": "2026-09-01", "item": "A", "category": "其他", "type": EXPENSE, "amount": 10, "note": "", "receipt_id": "r1"}, key_a, False, False, "可保存")
    candidate_b = ReceiptCandidate(1, {"date": "2026-09-01", "item": "B", "category": "其他", "type": EXPENSE, "amount": 20, "note": "", "receipt_id": "r1"}, key_b, False, False, "可保存")
    rows, skipped = receipt.finalize_receipt_candidates([candidate_a, candidate_b], {candidate_b.key})
    assert skipped == 1
    assert rows == []


def test_legacy_receipt_without_id_is_conservatively_duplicate(monkeypatch):
    monkeypatch.setattr(db, "today_my", lambda: date(2026, 9, 1))
    edited = pd.DataFrame([{"保存": True, "日期已确认": True, "仍然保存重复": False, "date": "2026-09-01", "item": "Meal", "category": "饮食", "type": EXPENSE, "amount": 20, "note": "", "receipt_id": "newreceipt"}])
    statuses, candidates = receipt.evaluate_receipt_candidates(edited, {("2026-09-01", "meal", EXPENSE, 20.0, "")})
    assert statuses == ["疑似重复（未保存）"]
    assert not candidates


def test_forecast_uses_median_not_outlier_mean_and_allows_negative(monkeypatch):
    monkeypatch.setattr(analytics, "today_my", lambda: date(2026, 9, 1))
    rows = [{"date": "2026-09-01", "item": "Fixed", "category": "居住", "type": EXPENSE, "amount": 1000}]
    remaining = [700, 750, 720, 680, 730, 5000]
    for month, amount in zip([3, 4, 5, 6, 7, 8], remaining):
        rows.append({"date": f"2026-{month:02d}-15", "item": "Variable", "category": "其他", "type": EXPENSE, "amount": amount})
    result = analytics.historical_month_end_forecast(_frame(rows), 2026, 9, 1)
    assert result["forecast"] < 2000
    assert result["low"] <= result["forecast"] <= result["high"]

    refund_rows = rows + [{"date": "2026-09-01", "item": "Refund", "category": "居住", "type": REFUND, "amount": 2500}]
    negative = analytics.historical_month_end_forecast(_frame(refund_rows), 2026, 9, 1)
    assert negative["forecast"] < 0


def test_v3_entrypoint_has_no_v2_runtime_or_override_dependency(monkeypatch):
    source = (V3_ROOT / "app.py").read_text(encoding="utf-8")
    assert "v2" not in source
    assert "v3_overrides" not in source
    calls = []
    monkeypatch.setattr(web, "run", lambda: calls.append("run"))
    monkeypatch.setattr(st, "fragment", lambda fn: fn)
    runpy.run_path(str(V3_ROOT / "app.py"), run_name="__main__")
    assert calls == ["run"]
