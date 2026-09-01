from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from .config import EXPENSE, INCOME, RECEIPT_TOTAL_TOLERANCE, REFUND
from .db import normalize_transaction


@dataclass
class ReceiptCandidate:
    row_index: int
    normalized: dict[str, Any]
    key: tuple[str, str, str, float]
    force_duplicate: bool
    duplicate: bool
    status: str


def _positive_money(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else round(max(float(parsed), 0.0), 2)


def _duplicate_key(normalized: dict[str, Any]) -> tuple[str, str, str, float]:
    """Receipt duplicate identity intentionally ignores category.

    Category is an AI/user classification and can legitimately differ between
    two otherwise identical representations of the same receipt line. Using it
    as part of duplicate identity allows the same transaction to slip through
    merely because it was classified differently.
    """
    return (
        str(normalized["date"]),
        str(normalized["item"]).strip().casefold(),
        str(normalized["type"]),
        round(float(normalized["amount"]), 2),
    )


def _coerce_existing_duplicate_keys(existing_keys: set[tuple]) -> set[tuple[str, str, str, float]]:
    result: set[tuple[str, str, str, float]] = set()
    for key in existing_keys:
        if len(key) == 5:
            # Legacy db.transaction_key shape:
            # date, item, category, type, amount
            result.add((str(key[0]), str(key[1]).casefold(), str(key[3]), round(float(key[4]), 2)))
        elif len(key) == 4:
            result.add((str(key[0]), str(key[1]).casefold(), str(key[2]), round(float(key[3]), 2)))
    return result


def materialize_receipt_adjustments(
    transactions: Iterable[dict[str, Any]],
    *,
    tax: float = 0.0,
    service_charge: float = 0.0,
    discount: float = 0.0,
    fallback_category: str = "其他",
) -> list[dict[str, Any]]:
    """Turn receipt metadata into visible/editable ledger rows.

    Receipt reconciliation is not enough by itself: charges that contribute to
    the paid total must also be saved. These generated rows are deliberately
    visible in the editor so the user can change their date/category, uncheck
    them, or correct an OCR mistake before saving.
    """
    rows = [dict(row) for row in transactions]
    if not rows:
        return rows

    first_date = next((row.get("date") for row in rows if row.get("date")), None)
    categories = [str(row.get("category") or "").strip() for row in rows if str(row.get("category") or "").strip()]
    category = pd.Series(categories).mode().iloc[0] if categories else fallback_category

    def add(item: str, tx_type: str, amount: float, note: str) -> None:
        if amount <= RECEIPT_TOTAL_TOLERANCE:
            return
        rows.append({
            "date": first_date,
            "item": item,
            "category": category,
            "type": tx_type,
            "amount": amount,
            "note": note,
        })

    add("收据税费", EXPENSE, _positive_money(tax), "由收据税费自动加入，可在保存前修改")
    add("收据服务费", EXPENSE, _positive_money(service_charge), "由收据服务费自动加入，可在保存前修改")
    add("收据折扣", REFUND, _positive_money(discount), "由收据折扣自动加入，可在保存前修改")
    return rows


def evaluate_receipt_candidates(edited: pd.DataFrame, existing_keys: set[tuple]) -> tuple[list[str], list[ReceiptCandidate]]:
    statuses: list[str] = []
    candidates: list[ReceiptCandidate] = []
    existing_duplicate_keys = _coerce_existing_duplicate_keys(existing_keys)
    seen: set[tuple[str, str, str, float]] = set()
    for row_index, row in edited.iterrows():
        if not bool(row.get("保存")):
            statuses.append("未选择")
            continue
        if not bool(row.get("日期已确认")):
            statuses.append("需确认日期")
            continue
        try:
            normalized = normalize_transaction(row.to_dict())
            key = _duplicate_key(normalized)
        except Exception as exc:
            statuses.append(f"无效：{exc}")
            continue
        duplicate = key in existing_duplicate_keys or key in seen
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
    fresh_duplicate_keys = _coerce_existing_duplicate_keys(fresh_existing_keys)
    seen: set[tuple[str, str, str, float]] = set()
    for candidate in candidates:
        duplicate_now = candidate.key in fresh_duplicate_keys or candidate.key in seen
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
