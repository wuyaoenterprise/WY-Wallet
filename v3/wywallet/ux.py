from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st


def ranked_categories(categories: list[str], transactions: pd.DataFrame | None = None) -> list[str]:
    """Return categories with frequently used values first, then stable alphabetical order."""
    values = [str(value).strip() for value in categories if str(value).strip()]
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)

    counts: dict[str, int] = {}
    if isinstance(transactions, pd.DataFrame) and not transactions.empty and "category" in transactions:
        raw = transactions["category"].fillna("").astype(str).str.strip().str.casefold().value_counts()
        counts = {str(key): int(value) for key, value in raw.items() if str(key)}
    return sorted(unique, key=lambda value: (-counts.get(value.casefold(), 0), value.casefold()))


def page_slice(label: str, key: str, count: int, page_size: int) -> tuple[int, int, int]:
    """Render a safe page selector and clamp stale widget state after filters change."""
    page_count = max(1, (max(0, int(count)) + int(page_size) - 1) // int(page_size))
    if key in st.session_state:
        try:
            current = int(st.session_state[key])
        except Exception:
            current = 1
        if current < 1 or current > page_count:
            st.session_state[key] = 1
    page = (
        int(st.selectbox(label, range(1, page_count + 1), format_func=lambda value: f"第 {value}/{page_count} 页", key=key))
        if page_count > 1
        else 1
    )
    start = (page - 1) * int(page_size)
    return page, start, min(start + int(page_size), max(0, int(count)))


def exact_duplicate_count(
    transactions: pd.DataFrame | None,
    *,
    tx_date: date,
    item: str,
    category: str,
    tx_type: str,
    amount: float | None,
) -> int:
    """Count exact logical duplicates for a non-blocking manual-entry warning."""
    if not isinstance(transactions, pd.DataFrame) or transactions.empty or amount is None:
        return 0
    item_text = str(item or "").strip()
    category_text = str(category or "").strip()
    if not item_text or not category_text:
        return 0
    try:
        amount_value = round(float(amount), 2)
    except Exception:
        return 0
    if amount_value <= 0:
        return 0

    work = transactions.copy()
    dates = pd.to_datetime(work["date"], errors="coerce").dt.date
    amounts = pd.to_numeric(work["amount"], errors="coerce").round(2)
    mask = (
        (dates == tx_date)
        & work["item"].fillna("").astype(str).str.strip().str.casefold().eq(item_text.casefold())
        & work["category"].fillna("").astype(str).str.strip().str.casefold().eq(category_text.casefold())
        & work["type"].fillna("").astype(str).eq(str(tx_type))
        & amounts.eq(amount_value)
    )
    return int(mask.sum())
