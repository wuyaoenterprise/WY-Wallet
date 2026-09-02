from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

import pandas as pd

_OLD_LINE_SUFFIX = re.compile(r"^(?P<root>[A-Za-z0-9_-]{6,60})-(?P<line>\d{3,4})$")
_NEW_LINE_SUFFIX = re.compile(r"^(?P<root>[A-Za-z0-9_-]{6,60})-(?P<fingerprint>[0-9a-f]{10})-(?P<occurrence>\d{2,3})$")


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _date_text(value: object) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _line_identity(row: dict[str, Any]) -> dict[str, Any]:
    amount = pd.to_numeric(row.get("amount"), errors="coerce")
    return {
        "date": _date_text(row.get("date")),
        "item": _norm(row.get("item")),
        "type": str(row.get("type") or ""),
        "amount": 0.0 if pd.isna(amount) else round(float(amount), 2),
    }


def _line_fingerprint(row: dict[str, Any]) -> str:
    packed = json.dumps(_line_identity(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:10]


def _line_signature(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [_line_identity(dict(row)) for row in rows]
    result.sort(key=lambda value: (value["date"], value["item"], value["type"], value["amount"]))
    return result


def receipt_root_id(payload: dict[str, Any], rows: Iterable[dict[str, Any]]) -> str:
    row_list = [dict(row) for row in rows]
    merchant = _norm(payload.get("merchant"))
    number = _norm(payload.get("receipt_number"))
    total = round(float(payload.get("receipt_total") or 0), 2)
    dates = sorted({_date_text(row.get("date")) for row in row_list if row.get("date")})
    date_value = dates[0] if dates else ""
    if merchant and number:
        seed = {"merchant": merchant, "number": number, "date": date_value, "total": total}
    else:
        seed = {
            "merchant": merchant,
            "date": date_value,
            "total": total,
            "rows": _line_signature(row_list),
        }
    packed = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:16]


def receipt_root(receipt_id: object) -> str:
    value = str(receipt_id or "").strip()
    match = _NEW_LINE_SUFFIX.match(value) or _OLD_LINE_SUFFIX.match(value)
    return match.group("root") if match else value


def add_line_ids(rows: Iterable[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    """Assign order-independent semantic line IDs within one receipt root.

    Different lines keep the same ID even if OCR returns them in another order.
    Truly identical duplicate lines receive occurrence suffixes 01, 02, ... so
    they remain distinct ledger records.
    """
    root = receipt_root(root_id)
    occurrences: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        if root:
            fingerprint = _line_fingerprint(row)
            occurrences[fingerprint] = occurrences.get(fingerprint, 0) + 1
            row["receipt_id"] = f"{root}-{fingerprint}-{occurrences[fingerprint]:02d}"
        else:
            row["receipt_id"] = ""
        result.append(row)
    return result


def receipt_already_exists(root_id: str, receipt_ids: Iterable[object]) -> bool:
    root = receipt_root(root_id)
    return bool(root) and any(receipt_root(value) == root for value in receipt_ids if value)


def receipt_presence(
    root_id: str,
    current_line_ids: Iterable[object],
    existing_receipt_ids: Iterable[object],
) -> dict[str, Any]:
    """Describe whether a receipt is absent, partially saved or complete.

    Older root-only / numeric-line IDs are treated conservatively as complete,
    because their historical format cannot prove which semantic lines are
    missing. New fingerprint line IDs support safe completion of a partial save.
    """
    root = receipt_root(root_id)
    current = {
        str(value or "").strip()
        for value in current_line_ids
        if str(value or "").strip() and receipt_root(value) == root
    }
    existing = {
        str(value or "").strip()
        for value in existing_receipt_ids
        if str(value or "").strip() and receipt_root(value) == root
    }
    legacy_complete = root in existing or any(_OLD_LINE_SUFFIX.match(value) for value in existing)
    matched = current & existing
    complete = bool(root) and (legacy_complete or (bool(current) and current.issubset(existing)))
    partial = bool(root) and not complete and bool(matched)
    return {
        "root": root,
        "complete": complete,
        "partial": partial,
        "matched": len(matched),
        "total": len(current),
        "missing_ids": sorted(current - existing),
    }
