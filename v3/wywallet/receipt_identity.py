from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

import pandas as pd

_LINE_SUFFIX = re.compile(r"^(?P<root>[A-Za-z0-9_-]{6,60})-(?P<line>\d{3,4})$")


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _line_signature(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        amount = pd.to_numeric(row.get("amount"), errors="coerce")
        result.append({
            "date": str(row.get("date") or ""),
            "item": _norm(row.get("item")),
            "type": str(row.get("type") or ""),
            "amount": 0.0 if pd.isna(amount) else round(float(amount), 2),
        })
    result.sort(key=lambda value: (value["date"], value["item"], value["type"], value["amount"]))
    return result


def receipt_root_id(payload: dict[str, Any], rows: Iterable[dict[str, Any]]) -> str:
    row_list = [dict(row) for row in rows]
    merchant = _norm(payload.get("merchant"))
    number = _norm(payload.get("receipt_number"))
    total = round(float(payload.get("receipt_total") or 0), 2)
    dates = sorted({str(row.get("date") or "") for row in row_list if row.get("date")})
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
    match = _LINE_SUFFIX.match(value)
    return match.group("root") if match else value


def add_line_ids(rows: Iterable[dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    root = receipt_root(root_id)
    result: list[dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        row = dict(source)
        row["receipt_id"] = f"{root}-{index:03d}" if root else ""
        result.append(row)
    return result


def receipt_already_exists(root_id: str, receipt_ids: Iterable[object]) -> bool:
    root = receipt_root(root_id)
    return bool(root) and any(receipt_root(value) == root for value in receipt_ids if value)
