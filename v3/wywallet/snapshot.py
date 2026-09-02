from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .config import DEFAULT_CATEGORIES, MAX_TRANSACTION_ROWS, UI_CACHE_TTL_SECONDS, now_my
from . import db

_SESSION_SNAPSHOT_KEY = "v3_session_snapshot"


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


def _request_database_revision() -> dict[str, Any]:
    response = db.get_client().rpc("wy_wallet_get_ledger_revision").execute()
    data = response.data
    if isinstance(data, dict):
        row = data
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        row = data[0]
    else:
        raise RuntimeError("Supabase ledger revision RPC 返回了无法识别的数据格式。")
    return {
        "revision": int(row.get("revision") or 0),
        "updated_at": str(row.get("updated_at") or ""),
    }


@st.cache_data(ttl=UI_CACHE_TTL_SECONDS, max_entries=32, show_spinner=False)
def _load_snapshot(database_revision: int, limit: int) -> dict[str, Any]:
    # The database revision is part of the cache key. Any committed ledger write
    # invalidates this cache across Streamlit sessions without retransferring the
    # full ledger when nothing changed.
    return _request_snapshot(limit)


def _session_snapshot(limit: int) -> dict[str, Any] | None:
    value = st.session_state.get(_SESSION_SNAPSHOT_KEY)
    if not isinstance(value, dict) or int(value.get("limit") or 0) != int(limit):
        return None
    snap = value.get("snapshot")
    return snap if isinstance(snap, dict) else None


def _store_session_snapshot(snapshot: dict[str, Any], limit: int) -> None:
    st.session_state[_SESSION_SNAPSHOT_KEY] = {"limit": int(limit), "snapshot": snapshot}


def current_snapshot(limit: int = MAX_TRANSACTION_ROWS) -> dict[str, Any]:
    limit = int(limit)
    try:
        revision = _request_database_revision()
    except Exception:
        # Degrade safely if the lightweight revision RPC is temporarily
        # unavailable: read a fresh authoritative snapshot rather than serving a
        # potentially stale session copy.
        snap = _request_snapshot(limit)
        _store_session_snapshot(snap, limit)
        return snap

    local = _session_snapshot(limit)
    if local is not None and int(local.get("database_revision") or 0) == revision["revision"]:
        return local

    snap = _load_snapshot(revision["revision"], limit)
    _store_session_snapshot(snap, limit)
    return snap


def fresh_snapshot(limit: int = MAX_TRANSACTION_ROWS) -> dict[str, Any]:
    """Bypass Streamlit cache while keeping the database read to one RPC."""
    snap = _request_snapshot(int(limit))
    _store_session_snapshot(snap, int(limit))
    return snap


def _normalize_inserted_row(raw_row: Any) -> pd.DataFrame:
    row = _payload_dict(raw_row)
    valid, invalid = db.split_transaction_rows([row])
    if valid.empty or not invalid.empty:
        raise RuntimeError("新增交易已写入，但返回的数据无法用于即时刷新。")
    valid["updated_at"] = str(row.get("updated_at") or "")
    valid["flow_subtype"] = str(row.get("flow_subtype") or "")
    valid["client_token"] = str(row.get("client_token") or "")
    structured_receipt = str(row.get("receipt_id") or "")
    if structured_receipt:
        valid["receipt_id"] = structured_receipt
    return valid


def patch_session_snapshot_after_insert(
    raw_row: Any,
    *,
    expected_revision_delta: int = 1,
    limit: int = MAX_TRANSACTION_ROWS,
) -> bool:
    """Patch the current session after an insert without re-downloading the ledger.

    The patch is accepted only when the database revision advanced by exactly the
    number of writes we just performed. If another browser changed the ledger at
    the same time, the session snapshot is discarded so the next rerun performs a
    full authoritative refresh instead of hiding that concurrent write.
    """
    limit = int(limit)
    local = _session_snapshot(limit)
    if local is None:
        return False

    old_revision = int(local.get("database_revision") or 0)
    try:
        revision = _request_database_revision()
        if revision["revision"] != old_revision + max(0, int(expected_revision_delta)):
            st.session_state.pop(_SESSION_SNAPSHOT_KEY, None)
            return False
        inserted = _normalize_inserted_row(raw_row)
    except Exception:
        st.session_state.pop(_SESSION_SNAPSHOT_KEY, None)
        return False

    transactions = local.get("transactions")
    if not isinstance(transactions, pd.DataFrame):
        st.session_state.pop(_SESSION_SNAPSHOT_KEY, None)
        return False

    work = transactions.copy()
    inserted_id = int(inserted.iloc[0]["id"])
    already_present = False
    if not work.empty and "id" in work:
        ids = pd.to_numeric(work["id"], errors="coerce")
        already_present = bool((ids == inserted_id).any())
        if already_present:
            work = work.loc[ids != inserted_id].copy()

    work = pd.concat([inserted, work], ignore_index=True)
    if len(work) > limit:
        keep_ids = set(pd.to_numeric(work["id"], errors="coerce").nlargest(limit).dropna().astype(int).tolist())
        work = work[work["id"].astype(int).isin(keep_ids)].copy()
    if not work.empty:
        work = work.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)

    categories = [str(value) for value in (local.get("categories") or [])]
    category = str(inserted.iloc[0].get("category") or "").strip()
    if category and category.casefold() not in {value.casefold() for value in categories}:
        categories.append(category)

    total_count = int(local.get("total_count") or len(transactions)) + (0 if already_present else 1)
    patched = dict(local)
    patched.update(
        {
            "transactions": work,
            "categories": categories,
            "truncated": total_count > len(work),
            "total_count": total_count,
            "database_revision": revision["revision"],
            "database_revision_updated_at": revision["updated_at"],
            "loaded_at": now_my().isoformat(timespec="seconds"),
        }
    )
    _store_session_snapshot(patched, limit)
    return True


def clear_snapshot_cache() -> None:
    _load_snapshot.clear()
    st.session_state.pop(_SESSION_SNAPSHOT_KEY, None)
