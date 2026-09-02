from __future__ import annotations

from typing import Any

import streamlit as st

from .config import DEFAULT_CATEGORIES, MAX_TRANSACTION_ROWS, UI_CACHE_TTL_SECONDS, now_my
from . import db


def _payload_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        item = data[0]
        if len(item) == 1 and isinstance(next(iter(item.values())), dict):
            return next(iter(item.values()))
        return item
    raise RuntimeError("Supabase snapshot RPC 返回了无法识别的数据格式。")


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_transactions = list(payload.get("transactions") or [])
    raw_categories = list(payload.get("categories") or [])
    registered = [str(row.get("name") or "").strip() for row in raw_categories if str(row.get("name") or "").strip()]

    valid, invalid = db.split_transaction_rows(raw_transactions)
    valid = db.canonicalize_transaction_categories(valid, registered)

    raw_by_id: dict[int, dict[str, Any]] = {}
    for row in raw_transactions:
        try:
            raw_by_id[int(row.get("id"))] = row
        except Exception:
            continue
    if not valid.empty:
        valid["updated_at"] = valid["id"].map(lambda value: str(raw_by_id.get(int(value), {}).get("updated_at") or ""))
        valid["flow_subtype"] = valid["id"].map(lambda value: str(raw_by_id.get(int(value), {}).get("flow_subtype") or ""))
        valid["client_token"] = valid["id"].map(lambda value: str(raw_by_id.get(int(value), {}).get("client_token") or ""))
        structured_receipts = valid["id"].map(lambda value: str(raw_by_id.get(int(value), {}).get("receipt_id") or ""))
        valid["receipt_id"] = structured_receipts.where(structured_receipts.str.len() > 0, valid["receipt_id"].fillna(""))

    tx_categories = [] if valid.empty else [str(value).strip() for value in valid["category"].dropna() if str(value).strip()]
    source = (registered if registered else DEFAULT_CATEGORIES.copy()) + tx_categories
    seen: set[str] = set()
    categories: list[str] = []
    for value in source:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            categories.append(value)

    total_count = int(payload.get("total_count") or len(raw_transactions))
    return {
        "transactions": valid,
        "invalid": invalid,
        "categories": categories,
        "truncated": total_count > len(raw_transactions),
        "total_count": total_count,
        "database_revision": int(payload.get("revision") or 0),
        "database_revision_updated_at": str(payload.get("revision_updated_at") or ""),
        "loaded_at": now_my().isoformat(timespec="seconds"),
    }


def _request_snapshot(limit: int) -> dict[str, Any]:
    response = db.get_client().rpc("wy_wallet_snapshot", {"p_limit": int(limit)}).execute()
    return _normalize_payload(_payload_dict(response.data))


@st.cache_data(ttl=UI_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _load_snapshot(revision: int, limit: int) -> dict[str, Any]:
    return _request_snapshot(limit)


def current_snapshot(limit: int = MAX_TRANSACTION_ROWS) -> dict[str, Any]:
    return _load_snapshot(db.data_revision(), int(limit))


def fresh_snapshot(limit: int = MAX_TRANSACTION_ROWS) -> dict[str, Any]:
    """Bypass Streamlit cache while keeping the database read to one RPC."""
    return _request_snapshot(int(limit))


def clear_snapshot_cache() -> None:
    _load_snapshot.clear()
