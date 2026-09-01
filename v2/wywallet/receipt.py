from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import EXPENSE, INCOME, RECEIPT_TOTAL_TOLERANCE, REFUND
from .db import normalize_transaction, transaction_key


@dataclass
class ReceiptCandidate:
    row_index: int
    normalized: dict[str, Any]
    key: tuple[str, str, str, str, float]
    force_duplicate: bool
    duplicate: bool
    status: str


def evaluate_receipt_candidates(edited: pd.DataFrame, existing_keys: set[tuple]) -> tuple[list[str], list[ReceiptCandidate]]:
    statuses: list[str] = []
    candidates: list[ReceiptCandidate] = []
    seen: set[tuple] = set()
    for row_index, row in edited.iterrows():
        if not bool(row.get("保存")):
            statuses.append("未选择")
            continue
        if not bool(row.get("日期已确认")):
            statuses.append("需确认日期")
            continue
        try:
            normalized = normalize_transaction(row.to_dict())
            key = transaction_key(normalized)
        except Exception as exc:
            statuses.append(f"无效：{exc}")
            continue
        duplicate = key in existing_keys or key in seen
        force = bool(row.get("仍然保存重复"))
        if duplicate and not force:
            statuses.append("疑似重复（未保存）")
            seen.add(key)
            continue
        status = "重复但已确认" if duplicate and force else "可保存"
        statuses.append(status)
        candidates.append(ReceiptCandidate(int(row_index), normalized, key, force, duplicate, status))
        seen.add(key)
    return statuses, candidates


def finalize_receipt_candidates(candidates: list[ReceiptCandidate], fresh_existing_keys: set[tuple]) -> tuple[list[dict], int]:
    final_rows: list[dict] = []
    skipped = 0
    seen: set[tuple] = set()
    for candidate in candidates:
        duplicate_now = candidate.key in fresh_existing_keys or candidate.key in seen
        if duplicate_now and not candidate.force_duplicate:
            skipped += 1
            continue
        final_rows.append(candidate.normalized)
        seen.add(candidate.key)
    return final_rows, skipped


def reconcile_receipt_total(
    candidates: list[ReceiptCandidate],
    receipt_total: float | None,
    *,
    tax: float = 0.0,
    service_charge: float = 0.0,
    discount: float = 0.0,
) -> dict | None:
    if receipt_total is None:
        return None
    expense = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == EXPENSE)
    refund = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == REFUND)
    # Income is not expected from receipt extraction, but keep a defensive reversal
    # if an older session contains one.
    income = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == INCOME)
    item_total = round(expense - refund - income, 2)
    tax = round(max(float(tax or 0), 0.0), 2)
    service_charge = round(max(float(service_charge or 0), 0.0), 2)
    discount = round(max(float(discount or 0), 0.0), 2)
    expected_total = round(item_total + tax + service_charge - discount, 2)
    receipt = round(float(receipt_total), 2)
    difference = round(expected_total - receipt, 2)
    return {
        "receipt_total": receipt,
        "item_total": item_total,
        "tax": tax,
        "service_charge": service_charge,
        "discount": discount,
        "expected_total": expected_total,
        "difference": difference,
        "matches": abs(difference) <= RECEIPT_TOTAL_TOLERANCE,
        "tolerance": RECEIPT_TOTAL_TOLERANCE,
    }
