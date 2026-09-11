from __future__ import annotations

import pandas as pd

from wywallet.config import EXPENSE
from wywallet.exporting import sanitize_spreadsheet_text
from wywallet.receipt import evaluate_receipt_candidates


def _edited(category: str) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "保存": True,
            "日期已确认": True,
            "仍然保存重复": False,
            "date": "2026-08-01",
            "item": "Petrol",
            "category": category,
            "type": EXPENSE,
            "amount": 50.0,
            "note": "",
        }
    ])


def test_receipt_duplicate_detection_ignores_category_classification():
    # Existing legacy key contains category=交通, while the newly recognized
    # receipt row was classified as 汽车. It must still be treated as the same
    # likely transaction rather than bypassing duplicate protection.
    existing = {("2026-08-01", "petrol", "交通", EXPENSE, 50.0)}
    statuses, candidates = evaluate_receipt_candidates(_edited("汽车"), existing)
    assert statuses == ["疑似重复（未保存）"]
    assert candidates == []


def test_spreadsheet_formula_sanitizer_handles_leading_whitespace_and_newlines():
    for value in ["=1+1", "  =1+1", "\n@SUM(A1:A2)", "\t+cmd", "\ufeff -10"]:
        assert sanitize_spreadsheet_text(value).startswith("'")
    assert sanitize_spreadsheet_text("normal text") == "normal text"
