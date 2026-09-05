from __future__ import annotations

import html
import uuid

import pandas as pd
import streamlit as st

from . import analytics, db
from .access import touch_access
from .config import ADD_CATEGORY_OPTION, EXPENSE, INCOME, REFUND, TRANSACTION_TYPES, TYPE_LABELS, today_my
from .ledger_codec import detach_receipt_if_identity_changed, physical_payload, receipt_identity_changed
from .snapshot import clear_snapshot_cache
from .transaction_commands import add_transaction_dialog
from .ui import money, page_header
from .ux import page_slice, ranked_categories


def _signed_money(tx_type: str, amount: float) -> str:
    sign = "+" if tx_type in {INCOME, REFUND} else "−"
    return sign + money(amount)


@st.dialog("编辑交易", width="large")
def _edit_dialog(row: dict, categories: list[str], transactions: pd.DataFrame) -> None:
    touch_access()
    tx_id = int(row["id"])
    expected_updated_at = str(row.get("updated_at") or "")
    if not expected_updated_at:
        st.error("这笔交易缺少并发版本信息，请刷新页面后再编辑。")
        return

    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=pd.to_datetime(row["date"]).date(), max_value=today_my(), key=f"oc_edit_date_{tx_id}")
    tx_type = c2.segmented_control("类型", TRANSACTION_TYPES, default=row["type"], format_func=lambda value: TYPE_LABELS[value], key=f"oc_edit_type_{tx_id}")
    options = ranked_categories(categories, transactions)
    if row["category"] not in options:
        options.insert(0, row["category"])
    options.append(ADD_CATEGORY_OPTION)
    selected = st.selectbox("类别", options, index=options.index(row["category"]), key=f"oc_edit_cat_{tx_id}")
    new_category = st.text_input("新类别名称", max_chars=80, key=f"oc_edit_new_cat_{tx_id}") if selected == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", value=str(row["item"]), max_chars=180, key=f"oc_edit_item_{tx_id}")
    amount = st.number_input("金额 (RM)", min_value=0.01, step=0.01, value=float(row["amount"]), key=f"oc_edit_amount_{tx_id}")
    note = st.text_area("备注", value=str(row.get("note") or ""), max_chars=1000, key=f"oc_edit_note_{tx_id}")

    receipt_linked = bool(str(row.get("receipt_id") or "").strip())
    identity_changed = receipt_linked and receipt_identity_changed(
        row,
        {"date": tx_date, "item": item, "type": tx_type or EXPENSE, "amount": amount},
    )
    if identity_changed:
        st.warning("这笔交易来自收据；你修改了日期、项目、类型或金额。保存后会解除原 Receipt ID，避免账本内容继续挂在错误的收据身份上。")
    elif receipt_linked:
        st.caption("这笔交易来自收据。只修改类别或备注会保留原 Receipt ID。")

    if not st.button("保存修改", type="primary", width="stretch", key=f"oc_edit_submit_{tx_id}"):
        return
    category = new_category.strip() if selected == ADD_CATEGORY_OPTION else selected
    try:
        logical = db.normalize_transaction({
            "date": tx_date, "item": item, "category": category,
            "type": tx_type or EXPENSE, "amount": amount, "note": note,
            "receipt_id": str(row.get("receipt_id") or ""),
        })
        logical, subtype, detached = detach_receipt_if_identity_changed(
            row, logical, str(row.get("flow_subtype") or "")
        )
        payload = physical_payload(logical, subtype)
        db.get_client().rpc("wy_wallet_update_transaction", {
            "p_id": tx_id,
            "p_expected_updated_at": expected_updated_at,
            "p_date": payload["date"],
            "p_item": payload["item"],
            "p_category": payload["category"],
            "p_type": payload["type"],
            "p_amount": payload["amount"],
            "p_note": payload["note"],
            "p_receipt_id": payload.get("receipt_id"),
            "p_flow_subtype": payload.get("flow_subtype"),
        }).execute()
        if selected == ADD_CATEGORY_OPTION:
            try:
                db.create_category(category)
            except Exception:
                st.warning("交易已更新，但类别登记失败；它仍会作为历史类别显示。")
        db.invalidate_data()
        st.toast("交易已更新；原收据关联已解除" if detached else "交易已更新")
        st.rerun()
    except Exception as exc:
        text = str(exc)
        if "WY_WALLET_CONFLICT" in text or "40001" in text:
            st.error("这笔交易已在另一台设备或另一个页面被修改。为避免覆盖他人的修改，本次没有保存；请刷新后重新编辑。")
        elif "WY_WALLET_NOT_FOUND" in text or "P0002" in text:
            st.error("这笔交易已经被删除，请刷新页面。")
        else:
            st.error(f"修改失败：{exc}")


@st.dialog("删除交易")
def _delete_dialog(row: dict) -> None:
    touch_access()
    tx_id = int(row["id"])
    expected_updated_at = str(row.get("updated_at") or "")
    if not expected_updated_at:
        st.error("这笔交易缺少并发版本信息，请刷新页面后再删除。")
        return
    st.write(f"**{row['item']}**")
    st.caption(f"{pd.to_datetime(row['date']).date()} · {row['category']} · {TYPE_LABELS[row['type']]} · {money(row['amount'])}")
    confirmed = st.checkbox("我确认删除这笔交易", key=f"oc_delete_confirm_{tx_id}")
    if not st.button("确认删除", type="primary", disabled=not confirmed, width="stretch", key=f"oc_delete_submit_{tx_id}"):
        return
    try:
        db.get_client().rpc("wy_wallet_delete_transaction", {
            "p_id": tx_id,
            "p_expected_updated_at": expected_updated_at,
        }).execute()
        st.session_state["recently_deleted"] = {
            "row": {key: row.get(key) for key in ["date", "item", "category", "type", "amount", "note", "receipt_id", "flow_subtype"]},
            "undo_token": str(uuid.uuid4()),
        }
        db.invalidate_data()
        st.toast("交易已删除，可撤销最近一次删除")
        st.rerun()
    except Exception as exc:
        text = str(exc)
        if "WY_WALLET_CONFLICT" in text or "40001" in text:
            st.error("这笔交易已在别处被修改，因此没有删除。请刷新确认最新内容后再操作。")
        elif "WY_WALLET_NOT_FOUND" in text or "P0002" in text:
            st.error("这笔交易已经不存在。")
        else:
            st.error(f"删除失败：{exc}")


def _restore_recent() -> None:
    state = st.session_state.get("recently_deleted")
    if not state:
        return
    if isinstance(state, dict) and isinstance(state.get("row"), dict):
        snapshot = dict(state["row"])
        token = str(state.get("undo_token") or "").strip() or str(uuid.uuid4())
    else:
        snapshot = dict(state)
        token = str(uuid.uuid4())
        st.session_state["recently_deleted"] = {"row": snapshot, "undo_token": token}

    logical = db.normalize_transaction(snapshot)
    payload = physical_payload(logical, str(snapshot.get("flow_subtype") or ""))
    db.get_client().rpc("wy_wallet_insert_transaction", {
        "p_date": payload["date"],
        "p_item": payload["item"],
        "p_category": payload["category"],
        "p_type": payload["type"],
        "p_amount": payload["amount"],
        "p_note": payload["note"],
        "p_receipt_id": payload.get("receipt_id"),
        "p_flow_subtype": payload.get("flow_subtype"),
        "p_client_token": token,
    }).execute()
    db.invalidate_data()
    st.session_state.pop("recently_deleted", None)
    st.toast("已恢复最近删除")
    st.rerun()


def _filters(transactions: pd.DataFrame, categories: list[str]) -> pd.DataFrame:
    with st.expander("筛选交易", expanded=True):
        s, y, m = st.columns([2, 1, 1])
        search = s.text_input("搜索", placeholder="项目、类别或备注", key="oc_search")
        years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        year = y.selectbox("年份", ["全部"] + years, key="oc_year")
        month = m.selectbox("月份", ["全部"] + list(range(1, 13)), key="oc_month")
        t, c, so = st.columns([1, 1.5, 1.5])
        tx_type = t.selectbox("类型", ["全部"] + TRANSACTION_TYPES, format_func=lambda value: "全部" if value == "全部" else TYPE_LABELS[value], key="oc_type")
        category = c.selectbox("类别", ["全部"] + ranked_categories(categories, transactions), key="oc_category")
        sort = so.selectbox("排序", ["日期：最新优先", "日期：最早优先", "金额：由高到低", "金额：由低到高"], key="oc_sort")

    filtered = transactions.copy()
    if search:
        filtered = analytics.literal_search(filtered, search)
    if year != "全部":
        filtered = filtered[filtered["date"].dt.year == int(year)]
    if month != "全部":
        filtered = filtered[filtered["date"].dt.month == int(month)]
    if tx_type != "全部":
        filtered = filtered[filtered["type"] == tx_type]
    if category != "全部":
        filtered = filtered[filtered["category"].str.casefold() == str(category).casefold()]
    rules = {
        "日期：最新优先": ("date", False), "日期：最早优先": ("date", True),
        "金额：由高到低": ("amount", False), "金额：由低到高": ("amount", True),
    }
    column, ascending = rules[sort]
    return filtered.sort_values([column, "id"], ascending=[ascending, ascending]).reset_index(drop=True)


def _render_table(filtered: pd.DataFrame, categories: list[str], transactions: pd.DataFrame) -> None:
    if filtered.empty:
        st.info("没有符合条件的交易。")
        return
    _, start, end = page_slice("分页", "oc_table_page", len(filtered), 40)
    page = filtered.iloc[start:end].copy()
    display = pd.DataFrame({
        "日期": page["date"].dt.strftime("%Y-%m-%d"), "项目": page["item"], "类别": page["category"],
        "类型": page["type"].map(TYPE_LABELS), "金额": page.apply(lambda row: _signed_money(row["type"], row["amount"]), axis=1), "备注": page["note"],
    })
    st.dataframe(display, hide_index=True, width="stretch", height=min(520, 38 + 35 * max(len(display), 1)))
    row_map = {int(row["id"]): row.to_dict() for _, row in page.iterrows()}
    selected = st.selectbox("选择一笔交易进行操作", [None] + list(row_map), format_func=lambda value: "— 请选择 —" if value is None else f"{pd.to_datetime(row_map[value]['date']).date()} · {row_map[value]['item']} · {_signed_money(row_map[value]['type'], row_map[value]['amount'])}", key="oc_action")
    if selected is not None:
        e, d, _ = st.columns([1, 1, 4])
        if e.button("编辑交易", type="primary", width="stretch", key="oc_edit_button"):
            _edit_dialog(row_map[int(selected)], categories, transactions)
        if d.button("删除交易", width="stretch", key="oc_delete_button"):
            _delete_dialog(row_map[int(selected)])


def _render_cards(filtered: pd.DataFrame, categories: list[str], transactions: pd.DataFrame) -> None:
    if filtered.empty:
        st.info("没有符合条件的交易。")
        return
    _, start, end = page_slice("卡片分页", "oc_card_page", len(filtered), 30)
    page = filtered.iloc[start:end]
    for _, series in page.iterrows():
        row = series.to_dict()
        with st.container(border=True):
            left, right = st.columns([3, 1.2])
            left.markdown(f"**{html.escape(str(row['item']))}**")
            left.caption(f"{pd.to_datetime(row['date']).date()} · {row['category']} · {TYPE_LABELS[row['type']]}")
            if row.get("note"):
                left.caption(str(row["note"]))
            right.markdown(f"### {_signed_money(row['type'], row['amount'])}")
            e, d, _ = st.columns([1, 1, 3])
            if e.button("编辑", key=f"oc_card_edit_{int(row['id'])}", width="stretch"):
                _edit_dialog(row, categories, transactions)
            if d.button("删除", key=f"oc_card_delete_{int(row['id'])}", width="stretch"):
                _delete_dialog(row)


def render(
    transactions: pd.DataFrame,
    categories: list[str],
    *,
    truncated: bool = False,
    total_count: int | None = None,
) -> None:
    touch_access()
    page_header("交易记录", "跨设备编辑使用数据库版本检查；若记录已被其他设备修改，本次操作不会覆盖它。")
    if truncated:
        shown = len(transactions)
        total = int(total_count or shown)
        st.warning(f"账本共有约 {total:,} 笔交易；本页当前只载入最近 {shown:,} 笔，因此搜索、筛选和分页仅针对这部分数据。完整数据请使用「设置与备份 → 备份」。")
    add, receipt, undo, refresh, _ = st.columns([1, 1.2, 1.25, 1, 2.8])
    if add.button("＋ 新增交易", type="primary", width="stretch", key="oc_add"):
        add_transaction_dialog(categories, transactions)
    with receipt:
        st.page_link("pages/receipt.py", label="📷 AI 收据识别", width="stretch")
    if st.session_state.get("recently_deleted") and undo.button("↩ 撤销删除", width="stretch", key="oc_undo"):
        try:
            _restore_recent()
        except Exception as exc:
            st.error(f"撤销失败：{exc}。撤销快照仍保留，可再次尝试。")
    if refresh.button("↻ 刷新", width="stretch", key="oc_refresh"):
        db.refresh_data()
        clear_snapshot_cache()
        st.rerun()

    filtered = _filters(transactions, categories)
    income, expense, balance = analytics.calculate_totals(filtered)
    flows = analytics.calculate_flow_totals(filtered)
    a, b, c, d, e = st.columns(5, gap="small")
    a.metric("筛选结果", f"{len(filtered):,} 笔")
    b.metric("净支出", money(expense))
    c.metric("收入", money(income))
    d.metric("退款", money(flows["refund"]))
    e.metric("净额", money(balance))
    view = st.segmented_control("显示方式", ["表格", "卡片"], default="表格", key="oc_view")
    if view == "卡片":
        _render_cards(filtered, categories, transactions)
    else:
        _render_table(filtered, categories, transactions)
