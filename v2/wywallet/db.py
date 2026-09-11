from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd
import streamlit as st
from supabase import Client, create_client

from .config import (
    DB_BATCH_SIZE,
    DEFAULT_CATEGORIES,
    EXPENSE,
    MAX_TRANSACTION_ROWS,
    REFUND,
    TRANSACTION_TYPES,
    UI_CACHE_TTL_SECONDS,
    now_my,
    today_my,
)

TX_COLUMNS = ["id", "date", "item", "category", "type", "amount", "note"]


@st.cache_resource(show_spinner=False)
def get_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def _fetch_transaction_rows(client: Client, max_rows: int | None = MAX_TRANSACTION_ROWS) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    offset = 0
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
    truncated = False
    if max_rows is not None and len(rows) >= max_rows:
        probe = client.table("transactions").select("id").range(max_rows, max_rows).limit(1).execute()
        truncated = bool(probe.data)
    return rows, truncated


@st.cache_data(ttl=UI_CACHE_TTL_SECONDS, show_spinner=False)
def load_raw_transaction_rows() -> dict[str, Any]:
    rows, truncated = _fetch_transaction_rows(get_client())
    return {"rows": rows, "truncated": truncated, "loaded_at": now_my().isoformat(timespec="seconds")}


def _normalize_loaded_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    issues: list[str] = []
    try:
        tx_id = int(row.get("id"))
    except Exception:
        tx_id = None
        issues.append("id无效")

    parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
    if pd.isna(parsed_date):
        issues.append("日期无效")
    elif parsed_date.date() > today_my():
        issues.append("未来日期")

    item = str(row.get("item") or "").strip()
    if not item:
        issues.append("项目为空")

    category = str(row.get("category") or "").strip()
    if not category:
        issues.append("类别为空")

    raw_type = str(row.get("type") or "").strip()
    raw_amount = pd.to_numeric(row.get("amount"), errors="coerce")
    tx_type = raw_type
    amount = raw_amount

    # Shared-table compatibility: legacy production only understands Expense/Income.
    # V2 therefore stores a refund physically as Expense with a negative amount,
    # while exposing it logically as Refund with a positive absolute amount.
    if not pd.isna(raw_amount) and raw_type == EXPENSE and float(raw_amount) < 0:
        tx_type = REFUND
        amount = abs(float(raw_amount))

    if tx_type not in set(TRANSACTION_TYPES):
        issues.append("类型无效")
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


def split_transaction_rows(rows: Iterable[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
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


@st.cache_data(ttl=600, show_spinner=False)
def load_category_rows() -> list[str]:
    response = get_client().table("categories").select("name").execute()
    values = [str(row.get("name") or "").strip() for row in (response.data or [])]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def fetch_category_rows_fresh() -> list[str]:
    response = get_client().table("categories").select("name").execute()
    values = [str(row.get("name") or "").strip() for row in (response.data or [])]
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def canonicalize_transaction_categories(frame: pd.DataFrame, registered: list[str] | None = None) -> pd.DataFrame:
    if frame.empty or "category" not in frame:
        return frame.copy()
    work = frame.copy()
    registered = registered if registered is not None else load_category_rows()
    canonical: dict[str, str] = {str(value).casefold(): str(value) for value in registered if str(value).strip()}
    for value in work["category"].fillna("").astype(str):
        cleaned = value.strip()
        if cleaned:
            canonical.setdefault(cleaned.casefold(), cleaned)
    work["category"] = work["category"].fillna("").astype(str).map(lambda value: canonical.get(value.strip().casefold(), value.strip()))
    return work


def load_transactions() -> pd.DataFrame:
    valid, _ = split_transaction_rows(load_raw_transaction_rows()["rows"])
    return canonicalize_transaction_categories(valid)


def load_invalid_transactions() -> pd.DataFrame:
    _, invalid = split_transaction_rows(load_raw_transaction_rows()["rows"])
    return invalid


def transactions_truncated() -> bool:
    return bool(load_raw_transaction_rows().get("truncated"))


def data_loaded_at() -> str:
    return str(load_raw_transaction_rows().get("loaded_at") or "")


def fetch_transactions_fresh(*, max_rows: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    rows, truncated = _fetch_transaction_rows(get_client(), max_rows=max_rows)
    valid, invalid = split_transaction_rows(rows)
    valid = canonicalize_transaction_categories(valid, fetch_category_rows_fresh())
    return valid, invalid, truncated


def fetch_transactions_interactive_fresh() -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    return fetch_transactions_fresh(max_rows=MAX_TRANSACTION_ROWS)


def load_categories(transactions: pd.DataFrame | None = None) -> list[str]:
    registered = load_category_rows()
    if transactions is None:
        transactions = load_transactions()
    transaction_values = [] if transactions.empty else [str(v).strip() for v in transactions["category"].dropna() if str(v).strip()]
    source = (registered if registered else DEFAULT_CATEGORIES.copy()) + transaction_values
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
    tx_date = parsed_date.date()
    if tx_date > today_my():
        raise ValueError("记账模式不允许未来日期；未来支出请在实际发生后再记录。")
    item = str(row.get("item") or "").strip()
    category = str(row.get("category") or "").strip()
    tx_type = str(row.get("type") or "").strip()
    amount = pd.to_numeric(row.get("amount"), errors="coerce")
    note = str(row.get("note") or "").strip()
    if not item:
        raise ValueError("项目／商家不能为空。")
    if not category:
        raise ValueError("类别不能为空。")
    if tx_type not in set(TRANSACTION_TYPES):
        raise ValueError("类型必须是 Expense、Income 或 Refund。")
    if pd.isna(amount) or float(amount) <= 0:
        raise ValueError("金额必须大于 0。")
    return {
        "date": tx_date.isoformat(),
        "item": item[:180], "category": category[:80], "type": tx_type,
        "amount": round(float(amount), 2), "note": note[:1000],
    }


def _encode_transaction_for_db(logical: dict[str, Any]) -> dict[str, Any]:
    payload = dict(logical)
    if payload.get("type") == REFUND:
        payload["type"] = EXPENSE
        payload["amount"] = -abs(float(payload["amount"]))
    return payload


def normalize_transactions(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> list[dict[str, Any]]:
    records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
    return [normalize_transaction(dict(row)) for row in records]


def insert_transactions(rows: Iterable[dict[str, Any]] | pd.DataFrame) -> int:
    logical_records = normalize_transactions(rows)
    if not logical_records:
        raise ValueError("没有可保存的记录。")
    physical_records = [_encode_transaction_for_db(row) for row in logical_records]
    get_client().table("transactions").insert(physical_records).execute()
    invalidate_data()
    return len(logical_records)


def _fetch_transaction_by_id(transaction_id: int) -> dict[str, Any] | None:
    response = get_client().table("transactions").select("id,date,item,category,type,amount,note").eq("id", int(transaction_id)).limit(1).execute()
    rows = list(response.data or [])
    return dict(rows[0]) if rows else None


def update_transaction(transaction_id: int, row: dict[str, Any]) -> None:
    logical = normalize_transaction(row)
    physical = _encode_transaction_for_db(logical)
    if _fetch_transaction_by_id(transaction_id) is None:
        raise RuntimeError("这笔交易已被其他页面删除或不存在，请刷新后重试。")
    get_client().table("transactions").update(physical).eq("id", int(transaction_id)).execute()
    after = _fetch_transaction_by_id(transaction_id)
    if after is None:
        raise RuntimeError("更新后无法重新读取这笔交易，请刷新确认数据库状态。")
    normalized_after, issues = _normalize_loaded_row(after)
    if normalized_after is None or issues:
        raise RuntimeError("数据库返回的更新结果无效，请刷新确认。")
    comparable = {k: normalized_after[k] for k in ["item", "category", "type", "amount", "note"]}
    expected = {k: logical[k] for k in ["item", "category", "type", "amount", "note"]}
    comparable["date"] = normalized_after["date"].date().isoformat()
    expected["date"] = logical["date"]
    if comparable != expected:
        raise RuntimeError("数据库未完整套用修改，可能发生并发更新；请刷新后重试。")
    invalidate_data()


def delete_transaction(transaction_id: int) -> None:
    if _fetch_transaction_by_id(transaction_id) is None:
        raise RuntimeError("这笔交易已经不存在，请刷新页面。")
    get_client().table("transactions").delete().eq("id", int(transaction_id)).execute()
    if _fetch_transaction_by_id(transaction_id) is not None:
        raise RuntimeError("数据库没有删除这笔交易，请刷新后重试。")
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


def _escape_ilike_literal(value: str) -> str:
    """Escape PostgreSQL LIKE wildcards so category names are treated literally."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _casefold_rows(client: Client, table: str, column: str, value: str, select_columns: str) -> list[dict[str, Any]]:
    """Find case-insensitive literal matches, then verify them exactly in Python.

    ILIKE is only a narrowing read. Mutations never use a wildcard expression.
    Pagination keeps the result correct even if a category has many transactions.
    """
    target_key = str(value).strip().casefold()
    if not target_key:
        return []
    pattern = _escape_ilike_literal(str(value).strip())
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            client.table(table)
            .select(select_columns)
            .ilike(column, pattern)
            .range(offset, offset + DB_BATCH_SIZE - 1)
            .execute()
        )
        batch = list(response.data or [])
        for row in batch:
            candidate = str(row.get(column) or "").strip()
            if candidate and candidate.casefold() == target_key:
                rows.append(dict(row))
        if len(batch) < DB_BATCH_SIZE:
            break
        offset += len(batch)
    return rows


def _transaction_ids_for_category(client: Client, name: str) -> list[int]:
    rows = _casefold_rows(client, "transactions", "category", name, "id,category")
    result: list[int] = []
    seen: set[int] = set()
    for row in rows:
        try:
            tx_id = int(row.get("id"))
        except Exception:
            continue
        if tx_id not in seen:
            seen.add(tx_id)
            result.append(tx_id)
    return result


def _registered_name_variants(client: Client, name: str) -> list[str]:
    rows = _casefold_rows(client, "categories", "name", name, "name")
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        candidate = str(row.get("name") or "").strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def _delete_empty_category_if_safe(client: Client, name: str) -> bool:
    if _transaction_ids_for_category(client, name):
        return False
    # The target was created by this flow, so exact deletion is intentional.
    client.table("categories").delete().eq("name", name).execute()
    return True


def merge_category_safely(source: str, target: str) -> MergeResult:
    source = str(source or "").strip()
    target = str(target or "").strip()
    if not source or not target or source.casefold() == target.casefold():
        raise ValueError("请选择不同的原类别和目标类别。")
    client = get_client()
    registered_rows = load_category_rows()
    registered_map = {value.casefold(): value for value in registered_rows}
    known_map = {value.casefold(): value for value in load_categories(load_transactions())}
    target = known_map.get(target.casefold(), target)
    target_created = False
    try:
        if target.casefold() not in registered_map:
            client.table("categories").insert({"name": target[:80]}).execute()
            target_created = True

        source_ids = _transaction_ids_for_category(client, source)
        moved_rows = len(source_ids)
        for start in range(0, len(source_ids), DB_BATCH_SIZE):
            chunk = source_ids[start:start + DB_BATCH_SIZE]
            if chunk:
                client.table("transactions").update({"category": target}).in_("id", chunk).execute()

        remaining_ids = _transaction_ids_for_category(client, source)
        if remaining_ids:
            raise RuntimeError("部分交易仍在原类别；系统已保留两边类别，未删除任何交易。请重试。")

        cleanup_note = ""
        source_removed = True
        try:
            for exact_name in _registered_name_variants(client, source):
                client.table("categories").delete().eq("name", exact_name).execute()
        except Exception:
            source_removed = False
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
