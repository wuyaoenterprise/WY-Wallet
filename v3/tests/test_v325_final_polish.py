from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

import wywallet.ai as base_ai
import wywallet.ai_release as ai_release
from wywallet import analytics, product_logic, ui
from wywallet.ai import FinanceQueryPlan
from wywallet.config import EXPENSE
from wywallet.ledger_codec import detach_receipt_if_identity_changed, receipt_identity_changed
from wywallet.receipt import evaluate_receipt_candidates
from wywallet.receipt_identity import add_line_ids, receipt_presence, receipt_root


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


def test_partial_first_tracking_month_is_excluded_when_full_months_exist(monkeypatch):
    monkeypatch.setattr(product_logic, "today_my", lambda: date(2026, 9, 2))
    monkeypatch.setattr(product_logic, "now_my", lambda: datetime(2026, 9, 2))
    transactions = _frame([
        {"date": "2023-05-31", "item": "Partial", "category": "其他", "type": EXPENSE, "amount": 900.0},
        *[
            {"date": f"2023-{month:02d}-15", "item": "Full", "category": "其他", "type": EXPENSE, "amount": 100.0}
            for month in range(6, 13)
        ],
    ])
    annual = analytics.monthly_summary(transactions, 2023)
    assert product_logic.first_complete_tracking_month(transactions, 2023) == 6
    assert product_logic.historical_monthly_average(annual, 2023, transactions) == pytest.approx(100.0)


def test_ai_monthly_average_uses_same_complete_month_rule(monkeypatch):
    monkeypatch.setattr(product_logic, "today_my", lambda: date(2026, 9, 2))
    monkeypatch.setattr(ai_release, "today_my", lambda: date(2026, 9, 2))
    monkeypatch.setattr(base_ai, "today_my", lambda: date(2026, 9, 2))
    transactions = _frame([
        {"date": "2026-05-31", "item": "Partial", "category": "其他", "type": EXPENSE, "amount": 900.0},
        {"date": "2026-06-15", "item": "Full", "category": "其他", "type": EXPENSE, "amount": 100.0},
        {"date": "2026-07-15", "item": "Full", "category": "其他", "type": EXPENSE, "amount": 100.0},
    ])
    plan = FinanceQueryPlan(
        intent="amount",
        subject_mode="all",
        aggregation_mode="specific",
        aggregation="average_month",
        flow_mode="specific",
        flow="expense",
        time_mode="specific",
        date_from="2026-05-01",
        date_to="2026-07-31",
    )
    result = ai_release.execute_finance_plan(plan, transactions)
    assert result["authoritative_total"] == pytest.approx(100.0)


def test_receipt_line_ids_are_stable_across_ocr_reordering():
    root = "abcdef1234567890"
    rows = [
        {"date": "2026-09-01", "item": "Meal", "type": EXPENSE, "amount": 20.0},
        {"date": "2026-09-01", "item": "Drink", "type": EXPENSE, "amount": 5.0},
    ]
    first = {row["item"]: row["receipt_id"] for row in add_line_ids(rows, root)}
    second = {row["item"]: row["receipt_id"] for row in add_line_ids(list(reversed(rows)), root)}
    assert first == second
    assert all(receipt_root(value) == root for value in first.values())


def test_identical_receipt_lines_remain_distinct():
    root = "abcdef1234567890"
    rows = add_line_ids([
        {"date": "2026-09-01", "item": "Coke", "type": EXPENSE, "amount": 3.0},
        {"date": "2026-09-01", "item": "Coke", "type": EXPENSE, "amount": 3.0},
    ], root)
    assert rows[0]["receipt_id"] != rows[1]["receipt_id"]
    assert receipt_root(rows[0]["receipt_id"]) == root
    assert receipt_root(rows[1]["receipt_id"]) == root


def test_receipt_presence_distinguishes_partial_and_complete():
    root = "abcdef1234567890"
    rows = add_line_ids([
        {"date": "2026-09-01", "item": "A", "type": EXPENSE, "amount": 10.0},
        {"date": "2026-09-01", "item": "B", "type": EXPENSE, "amount": 20.0},
        {"date": "2026-09-01", "item": "C", "type": EXPENSE, "amount": 30.0},
    ], root)
    ids = [row["receipt_id"] for row in rows]
    partial = receipt_presence(root, ids, ids[:1])
    assert partial["partial"] is True
    assert partial["complete"] is False
    assert partial["matched"] == 1
    assert partial["total"] == 3
    complete = receipt_presence(root, ids, ids)
    assert complete["complete"] is True
    assert complete["partial"] is False


def test_existing_receipt_line_id_blocks_changed_ocr_line():
    root = "abcdef1234567890"
    line_id = add_line_ids([
        {"date": "2026-09-01", "item": "Meal", "type": EXPENSE, "amount": 10.0},
    ], root)[0]["receipt_id"]
    edited = pd.DataFrame([{
        "保存": True,
        "日期已确认": True,
        "仍然保存重复": False,
        "date": date(2026, 9, 1),
        "item": "Meal OCR changed",
        "category": "饮食",
        "type": EXPENSE,
        "amount": 11.0,
        "note": "",
        "receipt_id": line_id,
        "flow_subtype": None,
    }])
    existing = {("2026-09-01", "meal", EXPENSE, 10.0, line_id)}
    statuses, candidates = evaluate_receipt_candidates(edited, existing)
    assert statuses == ["疑似重复（未保存）"]
    assert candidates == []


def test_receipt_edit_detaches_only_when_identity_changes():
    original = {
        "date": pd.Timestamp("2026-09-01"),
        "item": "Meal",
        "category": "饮食",
        "type": EXPENSE,
        "amount": 20.0,
        "note": "old",
        "receipt_id": "abcdef1234567890-deadbeef00-01",
        "flow_subtype": "receipt_tax",
    }
    category_note_only = {
        "date": "2026-09-01",
        "item": "Meal",
        "category": "其他",
        "type": EXPENSE,
        "amount": 20.0,
        "note": "new",
        "receipt_id": original["receipt_id"],
    }
    assert receipt_identity_changed(original, category_note_only) is False
    kept, kept_subtype, detached = detach_receipt_if_identity_changed(original, category_note_only, "receipt_tax")
    assert detached is False
    assert kept["receipt_id"] == original["receipt_id"]
    assert kept_subtype == "receipt_tax"

    changed = dict(category_note_only, amount=21.0)
    assert receipt_identity_changed(original, changed) is True
    detached_row, detached_subtype, detached = detach_receipt_if_identity_changed(original, changed, "receipt_tax")
    assert detached is True
    assert detached_row["receipt_id"] == ""
    assert detached_subtype is None


def test_root_legacy_runtime_is_retired_and_dependencies_match_v3():
    repo_root = Path(__file__).resolve().parents[2]
    root_app = (repo_root / "app.py").read_text(encoding="utf-8")
    assert "ROOT_ENTRYPOINT_RETIRED = True" in root_app
    assert 'V3_ENTRYPOINT = "v3/app.py"' in root_app
    assert "Smart Asset Pro" not in root_app
    assert "google.generativeai" not in root_app
    assert (repo_root / "requirements.txt").read_text(encoding="utf-8") == (repo_root / "v3" / "requirements.txt").read_text(encoding="utf-8")


def test_mobile_metric_wrap_and_chart_overflow_are_enabled():
    assert ':has(div[data-testid="stMetric"])' in ui.CSS
    assert "flex-wrap:wrap!important" in ui.CSS
    assert "overflow:visible" in ui.CSS


def test_receipt_page_uses_partial_completion_and_reports_hide_zero_highest():
    repo_root = Path(__file__).resolve().parents[2]
    receipt_page = (repo_root / "v3" / "pages" / "receipt.py").read_text(encoding="utf-8")
    reports_page = (repo_root / "v3" / "wywallet" / "reports_page.py").read_text(encoding="utf-8")
    assert "receipt_presence" in receipt_page
    assert "partial_saved" in receipt_page
    assert 'float(highest["支出"]) > 0' in reports_page
    assert "本年度没有正净支出月份" in reports_page


def test_release_version_is_v325():
    repo_root = Path(__file__).resolve().parents[2]
    config = (repo_root / "v3" / "wywallet" / "config.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "v3.2.5"' in config
