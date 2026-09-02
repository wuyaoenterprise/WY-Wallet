from __future__ import annotations

import uuid
from typing import Any

import pandas as pd
import streamlit as st

from . import db
from .access import touch_access
from .config import ADD_CATEGORY_OPTION, EXPENSE, TRANSACTION_TYPES, TYPE_LABELS, today_my
from .ledger_codec import physical_payload
from .snapshot import clear_snapshot_cache, patch_session_snapshot_after_insert
from .ux import exact_duplicate_count, ranked_categories


_IDEMPOTENCY_CONFLICT_KEY = "v3_add_idempotency_conflict"


def _submission_token() -> str:
    key = "v3_add_transaction_client_token"
    token = str(st.session_state.get(key) or "").strip()
    if not token:
        token = str(uuid.uuid4())
        st.session_state[key] = token
    return token


def _clear_submission_token() -> None:
    st.session_state.pop("v3_add_transaction_client_token", None)


def _rpc_row(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if len(data) == 1 and isinstance(next(iter(data.values())), dict):
            return dict(next(iter(data.values())))
        return dict(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        if len(first) == 1 and isinstance(next(iter(first.values())), dict):
            return dict(next(iter(first.values())))
        return dict(first)
    return {}


def _response_matches_payload(data: Any, payload: dict[str, Any]) -> bool:
    """Verify an idempotent RPC replay belongs to the current form payload."""
    row = _rpc_row(data)
    if not row:
        return False
    try:
        if str(row.get("date") or "")[:10] != str(payload.get("date") or "")[:10]:
            return False
        if str(row.get("item") or "").strip() != str(payload.get("item") or "").strip():
            return False
        if str(row.get("category") or "").strip() != str(payload.get("category") or "").strip():
            return False
        if str(row.get("type") or "") != str(payload.get("type") or ""):
            return False
        if round(float(row.get("amount")), 2) != round(float(payload.get("amount")), 2):
            return False
        if str(row.get("note") or "") != str(payload.get("note") or ""):
            return False
        if str(row.get("receipt_id") or "").strip() != str(payload.get("receipt_id") or "").strip():
            return False
    except Exception:
        return False
    return True


def _render_idempotency_conflict() -> None:
    st.error(
        "检测到同一保存 token 之前已经在数据库完成，但当前表单内容后来发生了变化。"
        "为避免把旧结果误报成新提交成功，本次没有新增第二笔交易。"
    )
    st.info("请刷新账本确认第一次已保存的记录；若内容需要调整，请在交易记录中编辑那一笔，而不是再次新增。")
    if st.button("刷新账本并结束本次保存", type="primary", width="stretch", key="fast_add_idempotency_reset"):
        _clear_submission_token()
        st.session_state.pop(_IDEMPOTENCY_CONFLICT_KEY, None)
        db.invalidate_data()
        clear_snapshot_cache()
        st.rerun()


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str], transactions: pd.DataFrame | None = None) -> None:
    touch_access()
    if st.session_state.get(_IDEMPOTENCY_CONFLICT_KEY):
        _render_idempotency_conflict()
        return

    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=today_my(), max_value=today_my(), key="fast_add_date")
    tx_type = c2.segmented_control(
        "类型",
        options=TRANSACTION_TYPES,
        default=EXPENSE,
        format_func=lambda value: TYPE_LABELS[value],
        key="fast_add_type",
    )
    ordered_categories = ranked_categories(categories, transactions)
    options = ordered_categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="fast_add_category")
    new_category_name = (
        st.text_input("新类别名称", placeholder="保存后同时登记", max_chars=80, key="fast_add_new_category")
        if selected_category == ADD_CATEGORY_OPTION
        else ""
    )
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", max_chars=180, key="fast_add_item")
    amount = st.number_input(
        "金额 (RM)", min_value=0.01, step=0.01, value=None, placeholder="0.00", key="fast_add_amount"
    )
    note = st.text_area("备注（可选）", max_chars=1000, key="fast_add_note")
    if tx_type == "Refund":
        st.caption("退款不是收入；它会抵减所选类别的净支出。")

    category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
    duplicate_count = exact_duplicate_count(
        transactions,
        tx_date=tx_date,
        item=item,
        category=category,
        tx_type=tx_type or EXPENSE,
        amount=amount,
    )
    if duplicate_count:
        st.warning(
            f"账本中已有 {duplicate_count} 笔日期、项目、类别、类型和金额完全相同的交易。"
            "如果这确实是另一笔消费仍可保存；请先确认不是重复输入。"
        )

    if not st.button("保存交易", type="primary", width="stretch", key="fast_add_submit"):
        return

    try:
        logical = db.normalize_transaction(
            {
                "date": tx_date,
                "item": item,
                "category": category,
                "type": tx_type or EXPENSE,
                "amount": amount,
                "note": note,
            }
        )
        payload = physical_payload(logical)
        response = db.get_client().rpc(
            "wy_wallet_insert_transaction",
            {
                "p_date": payload["date"],
                "p_item": payload["item"],
                "p_category": payload["category"],
                "p_type": payload["type"],
                "p_amount": payload["amount"],
                "p_note": payload["note"],
                "p_receipt_id": payload.get("receipt_id"),
                "p_flow_subtype": payload.get("flow_subtype"),
                "p_client_token": _submission_token(),
            },
        ).execute()

        if not _response_matches_payload(response.data, payload):
            st.session_state[_IDEMPOTENCY_CONFLICT_KEY] = True
            db.invalidate_data()
            clear_snapshot_cache()
            _render_idempotency_conflict()
            return

        category_created = False
        if selected_category == ADD_CATEGORY_OPTION:
            try:
                category_created = db.create_category(category)
            except Exception:
                st.warning("交易已保存，但类别登记失败；它仍会以历史类别显示。")

        patch_session_snapshot_after_insert(
            response.data,
            expected_revision_delta=1 + int(category_created),
        )
        db.invalidate_data()
        _clear_submission_token()
        st.session_state.pop(_IDEMPOTENCY_CONFLICT_KEY, None)
        st.toast("交易已保存")
        st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
