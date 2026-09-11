from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from .config import EXPENSE, INCOME, RECEIPT_TOTAL_TOLERANCE, REFUND
from .db import normalize_transaction

DuplicateKey = tuple[str, str, str, float, str]


@dataclass
class ReceiptCandidate:
    row_index: int
    normalized: dict[str, Any]
    key: tuple
    force_duplicate: bool
    duplicate: bool
    status: str


def _positive_money(value: Any) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else round(max(float(parsed), 0.0), 2)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def semantic_receipt_id(payload: dict, rows: Iterable[dict[str, Any]]) -> str:
    row_list = [dict(row) for row in rows]
    merchant = _norm(payload.get("merchant")); number = _norm(payload.get("receipt_number")); total = round(float(payload.get("receipt_total") or 0), 2)
    dates = sorted({str(row.get("date") or "") for row in row_list if row.get("date")}); date_value = dates[0] if dates else ""
    if merchant and number:
        seed = {"merchant": merchant, "number": number, "date": date_value, "total": total}
    elif merchant and date_value and payload.get("receipt_total") is not None:
        seed = {"merchant": merchant, "date": date_value, "total": total}
    else:
        normalized_rows = []
        for row in row_list:
            amount = pd.to_numeric(row.get("amount"), errors="coerce")
            normalized_rows.append({"date": str(row.get("date") or ""), "item": _norm(row.get("item")), "type": str(row.get("type") or ""), "amount": 0.0 if pd.isna(amount) else round(float(amount), 2)})
        normalized_rows.sort(key=lambda row: (row["date"], row["item"], row["type"], row["amount"]))
        seed = {"rows": normalized_rows, "total": total, "merchant": merchant}
    return hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _duplicate_key(normalized: dict[str, Any]) -> DuplicateKey:
    return (str(normalized["date"]), str(normalized["item"]).strip().casefold(), str(normalized["type"]), round(float(normalized["amount"]), 2), str(normalized.get("receipt_id") or ""))


def _ensure_key(key: tuple) -> DuplicateKey:
    if len(key) >= 5:
        return (str(key[0]), str(key[1]).casefold(), str(key[2]), round(float(key[3]), 2), str(key[4] or ""))
    if len(key) == 4:
        return (str(key[0]), str(key[1]).casefold(), str(key[2]), round(float(key[3]), 2), "")
    raise ValueError("invalid duplicate key")


def _coerce_existing_duplicate_keys(existing_keys: set[tuple]) -> set[DuplicateKey]:
    result: set[DuplicateKey] = set(); valid_types = {EXPENSE, INCOME, REFUND}
    for key in existing_keys:
        try:
            if len(key) == 5 and str(key[2]) in valid_types:
                result.add(_ensure_key(key))
            elif len(key) == 5:
                result.add((str(key[0]), str(key[1]).casefold(), str(key[3]), round(float(key[4]), 2), ""))
            elif len(key) == 4:
                result.add(_ensure_key(key))
        except Exception:
            continue
    return result


def materialize_receipt_adjustments(
    transactions: Iterable[dict[str, Any]],
    *,
    tax: float = 0.0,
    service_charge: float = 0.0,
    discount: float = 0.0,
    fallback_category: str = "其他",
    receipt_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in transactions]
    if not rows:
        return rows
    first_date = next((row.get("date") for row in rows if row.get("date")), None)
    rid = str(receipt_id or "").strip()
    for row in rows:
        if rid:
            row["receipt_id"] = rid
        row.setdefault("flow_subtype", None)

    def add(item: str, tx_type: str, amount: float, note: str, flow_subtype: str) -> None:
        if amount <= RECEIPT_TOTAL_TOLERANCE:
            return
        rows.append({
            "date": first_date,
            "item": item,
            "category": fallback_category,
            "type": tx_type,
            "amount": amount,
            "note": note,
            "receipt_id": rid,
            "flow_subtype": flow_subtype,
        })

    add("收据税费", EXPENSE, _positive_money(tax), "由收据税费自动加入，可在保存前修改", "receipt_tax")
    add("收据服务费", EXPENSE, _positive_money(service_charge), "由收据服务费自动加入，可在保存前修改", "receipt_service_charge")
    add("收据折扣", REFUND, _positive_money(discount), "由收据折扣自动加入，可在保存前修改", "receipt_discount")
    return rows


def _is_duplicate(key: tuple, exact_keys: set[DuplicateKey], seen: set[tuple]) -> bool:
    normalized = _ensure_key(key)
    normalized_seen = {_ensure_key(value) for value in seen}
    combined = exact_keys | normalized_seen
    if normalized in combined:
        return True
    # A structured receipt line ID is authoritative provenance. If that line ID
    # already exists, treat it as the same line even if a later OCR pass changes
    # the text or amount slightly.
    if normalized[4] and any(existing[4] == normalized[4] for existing in combined):
        return True
    base = normalized[:4]
    return any(existing[:4] == base and not existing[4] for existing in combined)


def evaluate_receipt_candidates(edited: pd.DataFrame, existing_keys: set[tuple]) -> tuple[list[str], list[ReceiptCandidate]]:
    statuses: list[str] = []
    candidates: list[ReceiptCandidate] = []
    existing_duplicate_keys = _coerce_existing_duplicate_keys(existing_keys)
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
            normalized["flow_subtype"] = str(row.get("flow_subtype") or "").strip() or None
            key = _duplicate_key(normalized)
        except Exception as exc:
            statuses.append(f"无效：{exc}")
            continue
        duplicate = _is_duplicate(key, existing_duplicate_keys, seen)
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
    fresh_duplicate_keys = _coerce_existing_duplicate_keys(fresh_existing_keys)
    seen: set[tuple] = set()
    final_rows: list[dict] = []
    skipped = 0
    for candidate in candidates:
        duplicate_now = _is_duplicate(candidate.key, fresh_duplicate_keys, seen)
        if duplicate_now and not candidate.force_duplicate:
            skipped += 1
            continue
        final_rows.append(candidate.normalized)
        seen.add(candidate.key)
    if skipped:
        return [], skipped
    return final_rows, 0


def reconcile_receipt_total(candidates: list[ReceiptCandidate], receipt_total: float | None) -> dict | None:
    if receipt_total is None:
        return None
    expense = sum(candidate.normalized["amount"] for candidate in candidates if candidate.normalized["type"] == EXPENSE)
    refund = sum(candidate.normalized["amount"] for candidate in candidates if candidate.normalized["type"] == REFUND)
    income = sum(candidate.normalized["amount"] for candidate in candidates if candidate.normalized["type"] == INCOME)
    expected_total = round(expense - refund - income, 2)
    receipt = round(float(receipt_total), 2)
    difference = round(expected_total - receipt, 2)
    return {
        "receipt_total": receipt,
        "item_total": expected_total,
        "tax": 0.0,
        "service_charge": 0.0,
        "discount": 0.0,
        "expected_total": expected_total,
        "difference": difference,
        "matches": abs(difference) <= RECEIPT_TOTAL_TOLERANCE,
        "tolerance": RECEIPT_TOTAL_TOLERANCE,
    }
