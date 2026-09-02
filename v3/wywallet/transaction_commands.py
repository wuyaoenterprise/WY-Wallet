from __future__ import annotations

import uuid

import streamlit as st

from . import db
from .access import touch_access
from .config import ADD_CATEGORY_OPTION, EXPENSE, INCOME, REFUND, REFUND_DB_MARKER, TRANSACTION_TYPES, TYPE_LABELS, today_my


def _submission_token() -> str:
    key = "v3_add_transaction_client_token"
    token = str(st.session_state.get(key) or "").strip()
    if not token:
        token = str(uuid.uuid4())
        st.session_state[key] = token
    return token


def _clear_submission_token() -> None:
    st.session_state.pop("v3_add_transaction_client_token", None)


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    touch_access()
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=today_my(), max_value=today_my(), key="fast_add_date")
    tx_type = c2.segmented_control(
        "类型",
        options=TRANSACTION_TYPES,
        default=EXPENSE,
        format_func=lambda value: TYPE_LABELS[value],
        key="fast_add_type",
    )
    options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="fast_add_category")
    new_category_name = (
        st.text_input("新类别名称", placeholder="保存后同时登记", key="fast_add_new_category")
        if selected_category == ADD_CATEGORY_OPTION
        else ""
    )
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="fast_add_item")
    amount = st.number_input(
        "金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="0.00", key="fast_add_amount"
    )
    note = st.text_area("备注（可选）", key="fast_add_note")
    if tx_type == REFUND:
        st.caption("退款不是收入；它会抵减所选类别的净支出。")

    if not st.button("保存交易", type="primary", width="stretch", key="fast_add_submit"):
        return

    category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
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
        physical_type = logical["type"]
        physical_note = logical["note"]
        flow_subtype = None
        if logical["type"] == REFUND:
            physical_type = INCOME
            physical_note = f"{REFUND_DB_MARKER} {physical_note}".rstrip()
            flow_subtype = "customer_refund"

        db.get_client().rpc(
            "wy_wallet_insert_transaction",
            {
                "p_date": logical["date"],
                "p_item": logical["item"],
                "p_category": logical["category"],
                "p_type": physical_type,
                "p_amount": logical["amount"],
                "p_note": physical_note,
                "p_receipt_id": None,
                "p_flow_subtype": flow_subtype,
                "p_client_token": _submission_token(),
            },
        ).execute()

        if selected_category == ADD_CATEGORY_OPTION:
            try:
                db.create_category(category)
            except Exception:
                st.warning("交易已保存，但类别登记失败；它仍会以历史类别显示。")

        # Invalidate only after the RPC has committed. The main app then reloads
        # through one wy_wallet_snapshot RPC instead of 3 x 1,000-row GET pages.
        db.invalidate_data()
        _clear_submission_token()
        st.toast("交易已保存")
        st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
