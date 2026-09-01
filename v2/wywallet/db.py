from __future__ import annotations

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


def _remember_db_error(exc: Exception) -> None:
    st.session_state["database_error"] = str(exc)


def _clear_db_error() -> None:
    st.session_state.pop("database_error", None)


@st.cache_data(ttl=120, show_spinner=False)
def load_transactions() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    offset = 0
    try:
        client = get_client()
        while offset < MAX_TRANSACTION_ROWS:
            response = (
                client.table("transactions")
                .select("id,date,item,category,type,amount,note")
                .order("date", desc=True)
                .order("id", desc=True)
                .range(offset, offset + DB_BATCH_SIZE - 1)
                .execute()
            )
            batch = list(response.data or [])
            rows.extend(batch)
            if len(batch) < DB_BATCH_SIZE:
                break
            offset += DB_BATCH_SIZE
        _clear_db_error()
    except Exception as exc:
        _remember_db_error(exc)
        return pd.DataFrame(columns=TX_COLUMNS)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=TX_COLUMNS)
    for column in TX_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame["item"] = frame["item"].fillna("").astype(str).str.strip()
    frame["category"] = frame["category"].fillna("其他").astype(str).str.strip()
    frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, INCOME]), EXPENSE)
    frame["note"] = frame["note"].fillna("").astype(str)
    frame = frame.dropna(subset=["date", "amount"])
    frame["amount"] = frame["amount"].astype(float).round(2)
    return frame.sort_values(["date", "id"], ascending=[False, False]).reset_index(drop=True)


@st.cache_data(ttl=600, show_spinner=False)
def load_category_rows() -> list[str]:
    try:
        response = get_client().table("categories").select("name").execute()
        _clear_db_error()
        values = [str(row.get("name") or "").strip() for row in (response.data or [])]
        return [value for value in values if value]
    except Exception as exc:
        _remember_db_error(exc)
        return []


def load_categories(transactions: pd.DataFrame | None = None) -> list[str]:
    registered = load_category_rows()
    transaction_values: list[str] = []
    if transactions is None:
        transactions = load_transactions()
    if not transactions.empty and "category" in transactions:
        transaction_values = [str(value).strip() for value in transactions["category"].dropna().tolist() if str(value).strip()]
    seen: set[str] = set()
    merged: list[str] = []
    for value in registered + transaction_values + DEFAULT_CATEGORIES:
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
    load_transactions.clear()
    load_category_rows.clear()
    st.session_state.pop("database_error", None)
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
        "item": item[:180],
        "category": category[:80],
        "type": tx_type,
        "amount": round(float(amount), 2),
        "note": note[:1000],
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


def merge_category_safely(source: str, target: str) -> MergeResult:
    """Idempotent, no-data-loss category merge across PostgREST calls."""
    source = str(source or "").strip()
    target = str(target or "").strip()
    if not source or not target or source.casefold() == target.casefold():
        raise ValueError("请选择不同的原类别和目标类别。")

    client = get_client()
    registered = {value.casefold() for value in load_category_rows()}
    target_created = False
    if target.casefold() not in registered:
        client.table("categories").insert({"name": target[:80]}).execute()
        target_created = True

    before = client.table("transactions").select("id", count="exact").eq("category", source).execute()
    moved_rows = int(before.count or len(before.data or []))
    client.table("transactions").update({"category": target}).eq("category", source).execute()

    remaining = client.table("transactions").select("id", count="exact").eq("category", source).limit(1).execute()
    if int(remaining.count or len(remaining.data or [])) != 0:
        invalidate_data()
        raise RuntimeError("类别交易更新未完整完成；原类别未删除，请重试。")

    source_removed = False
    try:
        client.table("categories").delete().eq("name", source).execute()
        source_removed = True
    finally:
        invalidate_data()
    return MergeResult(moved_rows=moved_rows, target_created=target_created, source_category_removed=source_removed)


def transaction_key(row: dict[str, Any]) -> tuple[str, str, str, str, float]:
    normalized = normalize_transaction(row)
    return (
        normalized["date"],
        normalized["item"].casefold(),
        normalized["category"].casefold(),
        normalized["type"],
        normalized["amount"],
    )


def existing_transaction_keys(*, fresh: bool = False) -> set[tuple[str, str, str, str, float]]:
    if fresh:
        load_transactions.clear()
    frame = load_transactions()
    keys: set[tuple[str, str, str, str, float]] = set()
    if frame.empty:
        return keys
    for row in frame.to_dict("records"):
        try:
            keys.add(transaction_key(row))
        except ValueError:
            continue
    return keys
