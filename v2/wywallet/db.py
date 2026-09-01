from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd
import streamlit as st
from supabase import Client, create_client

from .config import DB_BATCH_SIZE, DEFAULT_CATEGORIES, EXPENSE, INCOME, MAX_TRANSACTION_ROWS, today_my

TX_COLUMNS = ["id", "date", "item", "category", "type", "amount", "note"]


@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def _fetch_transaction_rows(client: Client, max_rows: int | None = MAX_TRANSACTION_ROWS) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    offset = 0
    truncated = False
    while max_rows is None or offset < max_rows:
        remaining = DB_BATCH_SIZE if max_rows is None else min(DB_BATCH_SIZE, max_rows - offset)
        if remaining <= 0:
            break
        response = (
            client.table("transactions")
            .select("id,date,item,category,type,amount,note")
            .order("date", desc=True)
            .order("id", desc=True)
            .range(offset, offset + remaining - 1)
            .execute()
        )
        batch = list(response.data or [])
        rows.extend(batch)
        if len(batch) < remaining:
            return rows, False
        offset += len(batch)
    if max_rows is not None and len(rows) >= max_rows:
        probe = client.table("transactions").select("id").range(max_rows, max_rows).limit(1).execute()
        truncated = bool(probe.data)
    return rows, truncated


@st.cache_data(ttl=120, show_spinner=False)
def load_raw_transaction_rows() -> dict[str, Any]:
    rows, truncated = _fetch_transaction_rows(get_client())
    return {"rows": rows, "truncated": truncated}


def _normalize_loaded_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    raw_id = row.get("id")
    try:
        tx_id = int(raw_id)
    except Exception:
        tx_id = None
        issues.append("id无效")

    parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
    if pd.isna(parsed_date):
        issues.append("日期无效")

    item = str(row.get("item") or "").strip()
    if not item:
        issues.append("项目为空")

    category = str(row.get("category") or "").strip()
    if not category:
        issues.append("类别为空")

    tx_type = str(row.get("type") or "").strip()
    if tx_type not in {EXPENSE, INCOME}:
        issues.append("类型无效")

    amount = pd.to_numeric(row.get("amount"), errors="coerce")
    if pd.isna(amount) or float(amount) <= 0:
        issues.append("金额无效")

    note = str(row.get("note") or "")
    if issues:
        return None, issues
    return {
        "id": tx_id,
        "date": parsed_date,
        "item": item,
        "category": category,
        "type": tx_type,
        "amount": round(float(amount), 2),
        "note": note,
    }, []


def split_transaction_rows(rows: Iterable[dict[str,Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for raw in rows:
        normalized, issues = _normalize_loaded_row(dict(raw))
        if normalized is not None:
            valid.append(normalized)
        else:
            invalid.append({
                "id": raw.get("id"), "date": raw.get("date"), "item": raw.get("item"),
                "category": raw.get("category"), "type": raw.get("type"), "amount": raw.get("amount"),
                "note": raw.get("note"), "issues": "；".join(issues),
            })
    valid_frame = pd.DataFrame(valid, columns=TX_COLUMNS)
    if not valid_frame.empty:
        valid_frame = valid_frame.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)
    invalid_frame = pd.DataFrame(invalid, columns=TX_COLUMNS + ["issues"])
    return valid_frame, invalid_frame


def load_transactions() -> pd.DataFrame:
    valid, _ = split_transaction_rows(load_raw_transaction_rows()["rows"])
    return valid


def load_invalid_transactions() -> pd.DataFrame:
    _, invalid = split_transaction_rows(load_raw_transaction_rows()["rows"])
    return invalid


def transactions_truncated() -> bool:
    return bool(load_raw_transaction_rows().get("truncated"))


def fetch_transactions_fresh() -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    rows, truncated = _fetch_transaction_rows(get_client(), max_rows=None)
    valid, invalid = split_transaction_rows(rows)
    return valid, invalid, truncated


@st.cache_data(ttl=600, show_spinner=False)
def load_category_rows() -> list[str]:
    response = get_client().table("categories").select("name").execute()
    values = [str(row.get("name") or "").strip() for row in (response.data or [])]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def fetch_category_rows_fresh() -> list[str]:
    response = get_client().table("categories").select("name").execute()
    values = [str(row.get("name") or "").strip() for row in (response.data or [])]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def load_categories(transactions: pd.DataFrame | None = None) -> list[str]:
    registered = load_category_rows()
    if transactions is None:
        transactions = load_transactions()
    transaction_values = [] if transactions.empty else [str(v).strip() for v in transactions["category"].dropna() if str(v).strip()]
    source = registered + transaction_values
    if not source:
        source = DEFAULT_CATEGORIES.copy()
    seen: set[str] = set()
    merged: list[str] = []
    for value in source:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            merged.append(value)
    return merged


def unregistered_categories(transactions: pd.DataFrame | None = None) -> list[str]:
    if transactions is None:
        transactions = load_transactions()
    registered = {value.casefold() for value in load_category_rows()}
    if transactions.empty:
        return []
    values = sorted({str(v).strip() for v in transactions["category"].dropna() if str(v).strip()})
    return [value for value in values if value.casefold() not in registered]


def invalidate_data() -> None:
    load_raw_transaction_rows.clear()
    load_category_rows.clear()
    st.session_state["data_revision"] = int(st.session_state.get("data_revision", 0)) + 1


def refresh_data() -> None:
    invalidate_data()


def normalize_transaction(row: dict[str, Any]) -> dict[str, Any]:
    parsed_date = pd.to_datetime(row.get("date", today_my()), errors="coerce")
    if pd.isna(parsed_date):
        raise ValueError("日期格式无效。")
    item = str(row.get("item") or "").strip()
    category = str(row.get("category") or "").strip()
    tx_type = str(row.get("type") or "").strip()
    amount = pd.to_numeric(row.get("amount"), errors="coerce")
    note = str(row.get("note") or "").strip()
    if not item:
        raise ValueError("项目／商家不能为空。")
    if not category:
        raise ValueError("类别不能为空。")
    if tx_type not in {EXPENSE, INCOME}:
        raise ValueError("类型必须是 Expense 或 Income。")
    if pd.isna(amount) or float(amount) <= 0:
        raise ValueError("金额必须大于 0。")
    return {
        "date": parsed_date.date().isoformat(),
        "item": item[:180], "category": category[:80], "type": tx_type,
        "amount": round(float(amount), 2), "note": note[:1000],
    }


def normalize_transactions(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> list[dict[str, Any]]:
    records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
    return [normalize_transaction(dict(row)) for row in records]


def insert_transactions(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> int:
    records = normalize_transactions(rows)
    if not records:
        raise ValueError("没有可保存的记录。")
    get_client().table("transactions").insert(records).execute()
    invalidate_data()
    return len(records)


def update_transaction(transaction_id: int, row: dict[str, Any]) -> None:
    payload = normalize_transaction(row)
    get_client().table("transactions").update(payload).eq("id", int(transaction_id)).execute()
    invalidate_data()


def delete_transaction(transaction_id: int) -> None:
    get_client().table("transactions").delete().eq("id", int(transaction_id)).execute()
    invalidate_data()


def create_category(name: str) -> bool:
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ValueError("类别名称不能为空。")
    existing = {value.casefold() for value in load_category_rows()}
    if cleaned.casefold() in existing:
        return False
    get_client().table("categories").insert({"name": cleaned[:80]}).execute()
    invalidate_data()
    return True


@dataclass
class MergeResult:
    moved_rows: int
    target_created: bool
    source_category_removed: bool
    cleanup_note: str = ""


def _delete_empty_category_if_safe(client: Client, name: str) -> bool:
    count = client.table("transactions").select("id", count="exact").eq("category", name).limit(1).execute()
    if int(count.count or len(count.data or [])) == 0:
        client.table("categories").delete().eq("name", name).execute()
        return True
    return False


def merge_category_safely(source: str, target: str) -> MergeResult:
    """No-data-loss category merge with compensating cleanup around PostgREST calls."""
    source = str(source or "").strip()
    target = str(target or "").strip()
    if not source or not target or source.casefold() == target.casefold():
        raise ValueError("请选择不同的原类别和目标类别。")
    client = get_client()
    registered = {value.casefold() for value in load_category_rows()}
    target_created = False
    try:
        if target.casefold() not in registered:
            client.table("categories").insert({"name": target[:80]}).execute()
            target_created = True
        before = client.table("transactions").select("id", count="exact").eq("category", source).execute()
        moved_rows = int(before.count or len(before.data or []))
        client.table("transactions").update({"category": target}).eq("category", source).execute()
        remaining = client.table("transactions").select("id", count="exact").eq("category", source).limit(1).execute()
        if int(remaining.count or len(remaining.data or [])) != 0:
            raise RuntimeError("部分交易仍在原类别；系统已保留两边类别，未删除任何交易。请重试。")
        source_removed = False
        cleanup_note = ""
        try:
            client.table("categories").delete().eq("name", source).execute()
            source_removed = True
        except Exception:
            cleanup_note = "交易已安全移动，但原类别登记删除失败；它可能暂时以空类别保留。"
        return MergeResult(moved_rows, target_created, source_removed, cleanup_note)
    except Exception:
        if target_created:
            try:
                _delete_empty_category_if_safe(client, target)
            except Exception:
                pass
        raise
    finally:
        invalidate_data()


def transaction_key(row: dict[str, Any]) -> tuple[str, str, str, str, float]:
    normalized = normalize_transaction(row)
    return (
        normalized["date"], normalized["item"].casefold(), normalized["category"].casefold(),
        normalized["type"], normalized["amount"],
    )


def existing_transaction_keys(*, fresh: bool = False) -> set[tuple[str, str, str, str, float]]:
    frame = fetch_transactions_fresh()[0] if fresh else load_transactions()
    keys: set[tuple[str, str, str, str, float]] = set()
    for row in frame.to_dict("records") if not frame.empty else []:
        try:
            keys.add(transaction_key(row))
        except ValueError:
            continue
    return keys


def ledger_signature(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "empty"
    columns = [c for c in TX_COLUMNS if c in frame.columns]
    work = frame[columns].copy().sort_values("id")
    if "date" in work:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").astype(str)
    hashed = pd.util.hash_pandas_object(work.fillna(""), index=False).values.tobytes()
    return hashlib.sha256(hashed).hexdigest()
