from __future__ import annotations

import calendar
import hmac
import html
import json

import pandas as pd
import plotly.express as px
import streamlit as st

from . import analytics
from .ai import (
    FinanceQueryPlan,
    answer_finance_question,
    authoritative_summary_markdown,
    categorize_macro,
    execute_finance_plan,
    finance_list_frame,
    plan_finance_question,
    state_from_plan,
)
from .config import (
    ADD_CATEGORY_OPTION,
    APP_TITLE,
    APP_VERSION,
    BUILD_ID,
    EXPENSE,
    INCOME,
    REFUND,
    TIMEZONE_NAME,
    TRANSACTION_TYPES,
    TYPE_LABELS,
    now_my,
    today_my,
)
from .db import (
    create_category,
    data_loaded_at,
    delete_transaction,
    fetch_category_rows_fresh,
    fetch_transactions_fresh,
    fetch_transactions_interactive_fresh,
    insert_transactions,
    ledger_signature,
    load_categories,
    load_category_rows,
    load_invalid_transactions,
    load_transactions,
    merge_category_safely,
    normalize_transaction,
    refresh_data,
    transactions_truncated,
    unregistered_categories,
    update_transaction,
)
from .exporting import build_backup_excel, safe_csv_bytes
from .ui import empty_state, inject_css, money, page_header, render_chart, safe_detail_html, section_title


def _sorted_categories(transactions: pd.DataFrame) -> list[str]:
    categories = load_categories(transactions)
    counts = transactions["category"].value_counts().to_dict() if not transactions.empty else {}
    return sorted(categories, key=lambda value: (-counts.get(value, 0), value.casefold()))


def _positive_flow(tx_type: str) -> bool:
    return tx_type in {INCOME, REFUND}


def _signed_money(tx_type: str, amount: float) -> str:
    return ("+" if _positive_flow(tx_type) else "−") + money(amount)


def _require_optional_private_access() -> bool:
    try:
        configured = str(st.secrets.get("WEB_ACCESS_PASSWORD", "") or "")
    except Exception:
        configured = ""
    if not configured:
        return False
    if st.session_state.get("web_access_ok"):
        return True
    st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="centered")
    inject_css()
    page_header("WY Wallet 私人访问", "请输入部署环境中的 WEB_ACCESS_PASSWORD。")
    entered = st.text_input("访问密码", type="password")
    if st.button("进入", type="primary", use_container_width=True):
        if hmac.compare_digest(entered, configured):
            st.session_state["web_access_ok"] = True
            st.rerun()
        else:
            st.error("密码不正确。")
    st.stop()
    return True


def _clamp_page_state(key: str, page_count: int) -> None:
    if key not in st.session_state:
        return
    try:
        value = int(st.session_state[key])
    except Exception:
        value = 1
    if value < 1 or value > page_count:
        st.session_state[key] = 1


def _page_selector(key: str, count: int, page_size: int) -> tuple[int, int, int]:
    page_count = max(1, (count + page_size - 1) // page_size)
    _clamp_page_state(key, page_count)
    page = int(st.selectbox("分页", range(1, page_count + 1), format_func=lambda x: f"第 {x}/{page_count} 页", key=key)) if page_count > 1 else 1
    start = (page - 1) * page_size
    return page, start, min(start + page_size, count)


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=today_my(), max_value=today_my(), key="add_date")
    tx_type = c2.segmented_control("类型", options=TRANSACTION_TYPES, default=EXPENSE, format_func=lambda value: TYPE_LABELS[value], key="add_type")
    options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="add_category")
    new_category_name = st.text_input("新类别名称", placeholder="保存后同时登记", key="add_new_category") if selected_category == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="add_item")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="0.00", key="add_amount")
    note = st.text_area("备注（可选）", key="add_note")
    if tx_type == REFUND:
        st.caption("退款不是收入；它会抵减所选类别的净支出。")
    if st.button("保存交易", type="primary", use_container_width=True):
        category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
        try:
            normalized = normalize_transaction({"date": tx_date, "item": item, "category": category, "type": tx_type or EXPENSE, "amount": amount, "note": note})
            insert_transactions([normalized])
            if selected_category == ADD_CATEGORY_OPTION:
                try:
                    create_category(category)
                except Exception:
                    st.warning("交易已保存，但类别登记失败；它仍会以历史类别显示。")
            st.toast("交易已保存")
            st.rerun()
        except Exception as exc:
            st.error(f"保存失败：{exc}")


@st.dialog("编辑交易", width="large")
def edit_transaction_dialog(transaction_id: int, categories: list[str]) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔有效交易，它可能已被其他页面删除或变成无效数据。")
        return
    row = match.iloc[0]
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=row["date"].date(), max_value=today_my(), key=f"edit_date_{transaction_id}")
    tx_type = c2.segmented_control("类型", TRANSACTION_TYPES, default=row["type"], format_func=lambda v: TYPE_LABELS[v], key=f"edit_type_{transaction_id}")
    options = categories.copy()
    if row["category"] not in options:
        options.insert(0, row["category"])
    options.append(ADD_CATEGORY_OPTION)
    selected = st.selectbox("类别", options, index=options.index(row["category"]), key=f"edit_category_{transaction_id}")
    new_category = st.text_input("新类别名称", key=f"edit_new_category_{transaction_id}") if selected == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", value=row["item"], key=f"edit_item_{transaction_id}")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=float(row["amount"]), key=f"edit_amount_{transaction_id}")
    note = st.text_area("备注", value=row["note"], key=f"edit_note_{transaction_id}")
    if st.button("保存修改", type="primary", use_container_width=True):
        category = new_category.strip() if selected == ADD_CATEGORY_OPTION else selected
        try:
            normalized = normalize_transaction({"date": tx_date, "item": item, "category": category, "type": tx_type or EXPENSE, "amount": amount, "note": note})
            update_transaction(transaction_id, normalized)
            if selected == ADD_CATEGORY_OPTION:
                try:
                    create_category(category)
                except Exception:
                    st.warning("交易已更新，但类别登记失败；它仍会以历史类别显示。")
            st.toast("交易已更新")
            st.rerun()
        except Exception as exc:
            st.error(f"修改失败：{exc}")


@st.dialog("删除交易")
def delete_transaction_dialog(transaction_id: int) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易，请刷新。")
        return
    row = match.iloc[0].to_dict()
    st.write(f"**{row['item']}**")
    st.caption(f"{row['date'].date()} · {row['category']} · {TYPE_LABELS[row['type']]} · {money(row['amount'])}")
    confirm = st.checkbox("我确认删除这笔交易", key=f"confirm_delete_{transaction_id}")
    if st.button("确认删除", type="primary", disabled=not confirm, use_container_width=True, key=f"delete_{transaction_id}"):
        try:
            snapshot = {k: row.get(k) for k in ["date", "item", "category", "type", "amount", "note"]}
            delete_transaction(transaction_id)
            st.session_state["recently_deleted"] = snapshot
            st.toast("交易已删除，可撤销最近一次删除")
            st.rerun()
        except Exception as exc:
            st.error(f"删除失败：{exc}")


@st.dialog("修复无效交易", width="large")
def repair_invalid_dialog(raw_row: dict) -> None:
    try:
        tx_id = int(raw_row.get("id"))
    except Exception:
        st.error("记录 id 无效，无法通过网页安全更新。")
        return
    parsed_date = pd.to_datetime(raw_row.get("date"), errors="coerce")
    default_date = today_my() if pd.isna(parsed_date) or parsed_date.date() > today_my() else parsed_date.date()
    parsed_amount = pd.to_numeric(raw_row.get("amount"), errors="coerce")
    default_amount = abs(float(parsed_amount)) if not pd.isna(parsed_amount) and float(parsed_amount) != 0 else 0.01
    st.caption(f"当前问题：{raw_row.get('issues', '')}")
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=default_date, max_value=today_my(), key=f"repair_date_{tx_id}")
    raw_type = str(raw_row.get("type") or "")
    default_type = REFUND if raw_type == EXPENSE and not pd.isna(parsed_amount) and float(parsed_amount) < 0 else (raw_type if raw_type in TRANSACTION_TYPES else EXPENSE)
    tx_type = c2.selectbox("类型", TRANSACTION_TYPES, index=TRANSACTION_TYPES.index(default_type), format_func=lambda v: TYPE_LABELS[v], key=f"repair_type_{tx_id}")
    item = st.text_input("项目／商家", value=str(raw_row.get("item") or ""), key=f"repair_item_{tx_id}")
    category = st.text_input("类别", value=str(raw_row.get("category") or ""), key=f"repair_cat_{tx_id}")
    amount = st.number_input("金额", min_value=0.01, step=0.01, value=default_amount, key=f"repair_amount_{tx_id}")
    note = st.text_area("备注", value=str(raw_row.get("note") or ""), key=f"repair_note_{tx_id}")
    if st.button("保存修复", type="primary", use_container_width=True):
        try:
            update_transaction(tx_id, {"date": tx_date, "item": item, "category": category, "type": tx_type, "amount": amount, "note": note})
            st.toast("无效交易已修复并重新纳入报表")
            st.rerun()
        except Exception as exc:
            st.error(f"修复失败：{exc}")


def _render_transaction_cards(filtered: pd.DataFrame, categories: list[str], key_prefix: str = "card") -> None:
    if filtered.empty:
        empty_state("没有符合条件的交易")
        return
    _, start, end = _page_selector(f"{key_prefix}_page", len(filtered), 30)
    for _, row in filtered.iloc[start:end].iterrows():
        with st.container(border=True):
            top, amount_col = st.columns([3, 1.25])
            top.markdown(f"**{html.escape(str(row['item']))}**")
            top.caption(f"{row['date'].date()} · {row['category']} · {TYPE_LABELS.get(row['type'], row['type'])}")
            amount_col.markdown(f"### {_signed_money(row['type'], row['amount'])}")
            if row.get("note"):
                st.caption(str(row["note"]))
            e, d, _ = st.columns([1, 1, 3])
            if e.button("编辑", key=f"{key_prefix}_edit_{int(row['id'])}", use_container_width=True):
                edit_transaction_dialog(int(row["id"]), categories)
            if d.button("删除", key=f"{key_prefix}_delete_{int(row['id'])}", use_container_width=True):
                delete_transaction_dialog(int(row["id"]))


def _render_static_transaction_table(filtered: pd.DataFrame, categories: list[str]) -> None:
    if filtered.empty:
        empty_state("没有符合条件的交易")
        return
    _, start, end = _page_selector("table_page", len(filtered), 40)
    page = filtered.iloc[start:end].copy()
    display = pd.DataFrame({
        "日期": page["date"].dt.strftime("%Y-%m-%d"), "项目": page["item"], "类别": page["category"],
        "类型": page["type"].map(TYPE_LABELS), "金额": page.apply(lambda r: _signed_money(r["type"], r["amount"]), axis=1), "备注": page["note"],
    })
    st.table(display)
    row_map = {int(row["id"]): row for _, row in page.iterrows()}
    selected_id = st.selectbox("选择一笔交易进行操作", [None] + list(row_map), format_func=lambda value: "— 请选择 —" if value is None else f"{row_map[value]['date'].date()} · {row_map[value]['item']} · {_signed_money(row_map[value]['type'], row_map[value]['amount'])}", key="table_action_id")
    if selected_id is not None:
        row = row_map[int(selected_id)]
        st.markdown(safe_detail_html(row["item"], row["category"], TYPE_LABELS[row["type"]], row["amount"], str(row["date"].date()), row["note"], _positive_flow(row["type"])), unsafe_allow_html=True)
        e, d, _ = st.columns([1, 1, 4])
        if e.button("编辑交易", type="primary", use_container_width=True):
            edit_transaction_dialog(int(selected_id), categories)
        if d.button("删除交易", use_container_width=True):
            delete_transaction_dialog(int(selected_id))


def _dashboard(transactions: pd.DataFrame) -> None:
    page_header("财务总览", "净支出会自动扣除退款；本月变化与上月相同已过天数比较。")
    now = now_my()
    current = analytics.month_slice(transactions, now.year, now.month)
    previous_same_period = analytics.previous_month_same_elapsed_slice(transactions, now.year, now.month, now.day)
    income, expense, balance = analytics.calculate_totals(current)
    _, prior_expense, _ = analytics.calculate_totals(previous_same_period)
    flows = analytics.calculate_flow_totals(current)
    change = None if prior_expense == 0 else (expense - prior_expense) / abs(prior_expense)
    days = calendar.monthrange(now.year, now.month)[1]
    projected = expense / max(now.day, 1) * days
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("本月收入", money(income))
    m2.metric("本月净支出", money(expense), "无上月同期数据" if change is None else f"{change:+.1%} 对比上月同期", delta_color="inverse")
    m3.metric("本月结余", money(balance))
    m4.metric("本月退款", money(flows["refund"]))
    m5.metric("月底预计净支出", money(projected), "按当前速度", delta_color="off")
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        section_title("最近 12 个月净支出")
        twelve = analytics.recent_months_summary(transactions)
        fig = px.bar(twelve, x="月份", y="支出", text_auto=".0f")
        fig.update_yaxes(tickprefix="RM ")
        render_chart(fig, height=335)
    with right:
        section_title("本月类别净支出")
        summary = analytics.net_expense_by_category(current)
        positive = summary[summary["amount"] > 0].head(7)
        if positive.empty:
            empty_state("本月暂无净支出")
        else:
            denominator = float(summary.loc[summary["amount"] > 0, "amount"].sum()) or 1.0
            for _, row in positive.iterrows():
                label, val = st.columns([1.6, 1])
                label.write(f"**{row['category']}**")
                val.write(money(row["amount"]))
                st.progress(min(max(float(row["amount"] / denominator), 0.0), 1.0))
    section_title("最近交易")
    if transactions.empty:
        empty_state("还没有任何交易记录")
    else:
        recent = transactions.head(8).copy()
        display = pd.DataFrame({
            "日期": recent["date"].dt.strftime("%Y-%m-%d"), "项目": recent["item"], "类别": recent["category"],
            "类型": recent["type"].map(TYPE_LABELS), "金额": recent.apply(lambda r: _signed_money(r["type"], r["amount"]), axis=1),
        })
        st.table(display)


def _transactions_page(transactions: pd.DataFrame, categories: list[str]) -> None:
    page_header("交易记录", "退款单独记录并抵减支出；未来日期不允许进入已发生账本。")
    add, receipt, undo, refresh, _ = st.columns([1, 1.2, 1.25, 1, 2.8])
    if add.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    with receipt:
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", use_container_width=True)
    if st.session_state.get("recently_deleted") and undo.button("↩ 撤销删除", use_container_width=True):
        snapshot = st.session_state.get("recently_deleted")
        try:
            insert_transactions([snapshot])
            st.session_state.pop("recently_deleted", None)
            st.toast("已恢复最近删除")
            st.rerun()
        except Exception as exc:
            st.error(f"撤销失败：{exc}。撤销快照仍保留，可再次尝试。")
    if refresh.button("↻ 刷新", use_container_width=True):
        refresh_data(); st.rerun()
    with st.expander("筛选交易", expanded=True):
        s, y, m = st.columns([2, 1, 1])
        search = s.text_input("搜索", placeholder="项目、类别或备注")
        years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        year = y.selectbox("年份", ["全部"] + years)
        month = m.selectbox("月份", ["全部"] + list(range(1, 13)))
        t, c, so = st.columns([1, 1.5, 1.5])
        tx_type = t.selectbox("类型", ["全部"] + TRANSACTION_TYPES, format_func=lambda x: "全部" if x == "全部" else TYPE_LABELS[x])
        category = c.selectbox("类别", ["全部"] + categories)
        sort = so.selectbox("排序", ["日期：最新优先", "日期：最早优先", "金额：由高到低", "金额：由低到高"])
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
    rules = {"日期：最新优先": ("date", False), "日期：最早优先": ("date", True), "金额：由高到低": ("amount", False), "金额：由低到高": ("amount", True)}
    col, asc = rules[sort]
    filtered = filtered.sort_values([col, "id"], ascending=[asc, asc]).reset_index(drop=True)
    fi, fe, fb = analytics.calculate_totals(filtered)
    flows = analytics.calculate_flow_totals(filtered)
    a, b, c, d, e = st.columns(5)
    a.metric("筛选结果", f"{len(filtered):,} 笔")
    b.metric("净支出", money(fe))
    c.metric("收入", money(fi))
    d.metric("退款", money(flows["refund"]))
    e.metric("净额", money(fb))
    view = st.segmented_control("显示方式", ["表格", "卡片"], default="表格", key="transaction_view")
    _render_transaction_cards(filtered, categories) if view == "卡片" else _render_static_transaction_table(filtered, categories)


def _reports_page(transactions: pd.DataFrame, invalid_rows: pd.DataFrame) -> None:
    page_header("分析报表", "退款会抵减对应类别支出；当前年度只显示截至今天已发生的月份和数据。")
    if transactions.empty:
        empty_state("暂无有效数据可分析"); return
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
    now = now_my()
    year = int(st.selectbox("分析年份", years, index=years.index(now.year) if now.year in years else 0))
    annual = analytics.monthly_summary(transactions, year)
    year_all = transactions[transactions["date"].dt.year == year].copy()
    elapsed = analytics.elapsed_month_count(year)
    display_annual = annual[annual["month"] <= elapsed].copy() if year == now.year else annual.copy()
    annual_expense = float(display_annual["支出"].sum())
    annual_income = float(display_annual["收入"].sum())
    annual_refund = float(display_annual["退款"].sum())
    monthly_avg = analytics.average_monthly_expense(annual, year)
    savings = analytics.annual_savings_rate(annual)
    yoy = analytics.same_period_yoy(transactions, year)
    highest = display_annual.loc[display_annual["支出"].idxmax()] if not display_annual.empty else None
    current_year = year == now.year
    avg_label = "截至目前月均" if current_year else "全年月均"
    prefix = "截至目前" if current_year else "年度"
    yoy_text = "无同期数据" if not yoy or yoy["change"] is None else f"{yoy['change']:+.1%} 同期同比"
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(f"{prefix}净支出", money(annual_expense), yoy_text, delta_color="inverse")
    m2.metric(f"{prefix}收入", money(annual_income))
    m3.metric(f"{prefix}退款", money(annual_refund))
    m4.metric(avg_label, "N/A" if monthly_avg is None else money(monthly_avg))
    m5.metric("储蓄率", "N/A" if savings is None else f"{savings:.1f}%")
    if highest is not None:
        st.caption(f"最高净支出月份：{highest['月份']} · {money(highest['支出'])}")
    if yoy:
        st.caption(f"同比口径：{yoy['current_start']}–{yoy['current_end']} 对比 {yoy['previous_start']}–{yoy['previous_end']}。")

    section = st.segmented_control("报表区块", ["快速总览", "年度趋势", "月度明细", "类别分析", "异常与规律"], default="快速总览", key="report_section")
    if section == "快速总览":
        l, r = st.columns([1.55, 1], gap="large")
        with l:
            section_title("每月净支出")
            fig = px.bar(display_annual, x="月份", y="支出", text_auto=".0f")
            if monthly_avg is not None:
                fig.add_hline(y=monthly_avg, line_dash="dash", annotation_text=f"月均 {money(monthly_avg)}")
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=390)
        with r:
            section_title("年度类别净支出")
            cat = analytics.net_expense_by_category(year_all)
            positive = cat[cat["amount"] > 0].head(8)
            if positive.empty:
                st.info("没有正的类别净支出。")
            else:
                fig = px.pie(positive, values="amount", names="category", hole=.56)
                render_chart(fig, height=390, legend=True, hovermode="closest")
    elif section == "年度趋势":
        section_title("收入、净支出与结余")
        cash = display_annual.melt(id_vars=["month", "月份"], value_vars=["收入", "支出", "结余"], var_name="指标", value_name="金额")
        fig = px.line(cash, x="月份", y="金额", color="指标", markers=True)
        fig.update_yaxes(tickprefix="RM ")
        render_chart(fig, height=390, legend=True)
        prior = analytics.monthly_summary(transactions, year - 1)
        if current_year:
            prior = prior[prior["month"] <= elapsed].copy()
        compare = display_annual[["月份", "累计支出"]].rename(columns={"累计支出": str(year)}).copy()
        compare[str(year - 1)] = prior["累计支出"].values[:len(compare)]
        melt = compare.melt(id_vars="月份", var_name="年份", value_name="累计净支出")
        fig = px.line(melt, x="月份", y="累计净支出", color="年份", markers=True)
        fig.update_yaxes(tickprefix="RM ")
        render_chart(fig, height=350, legend=True)
    elif section == "月度明细":
        month_options = list(range(1, now.month + 1)) if current_year else list(range(1, 13))
        default_month_index = len(month_options) - 1 if current_year else min(now.month - 1, 11)
        month = int(st.selectbox("选择月份", month_options, index=default_month_index, format_func=lambda v: f"{v}月", key="report_month"))
        selected = analytics.month_slice(transactions, year, month)
        mi, me, mb = analytics.calculate_totals(selected)
        flows = analytics.calculate_flow_totals(selected)
        is_current_month = current_year and month == now.month
        days = calendar.monthrange(year, month)[1]
        projected = me / max(now.day, 1) * days if is_current_month else None
        a, b, c, d, e = st.columns(5)
        a.metric("收入", money(mi)); b.metric("净支出", money(me)); c.metric("退款", money(flows["refund"])); d.metric("结余", money(mb)); e.metric("月底预计" if is_current_month else "实际净支出", money(projected if is_current_month else me), "按当前速度" if is_current_month else "实际", delta_color="off")
        effects = analytics.expense_effect_frame(selected)
        if effects.empty:
            st.info("该月没有支出或退款。")
        else:
            daily = effects.assign(day=effects["date"].dt.day).groupby("day")["expense_effect"].sum().reset_index(name="amount")
            fig = px.bar(daily, x="day", y="amount", labels={"day": "日期", "amount": "净支出 (RM)"})
            fig.update_xaxes(dtick=1); fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=370)
            wd = analytics.weekday_average(selected, year, month)
            fig = px.bar(wd, x="星期", y="平均每个该星期", labels={"平均每个该星期": "平均净支出 (RM)"})
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=330)
    elif section == "类别分析":
        effects = analytics.expense_effect_frame(year_all)
        if effects.empty:
            st.info("该年度没有支出或退款。")
        else:
            effects["毛支出"] = effects["amount"].where(effects["type"] == EXPENSE, 0.0)
            effects["退款"] = effects["amount"].where(effects["type"] == REFUND, 0.0)
            data = effects.groupby("category").agg(毛支出=("毛支出", "sum"), 退款=("退款", "sum"), 净支出=("expense_effect", "sum"), 交易笔数=("amount", "size")).reset_index().sort_values("净支出", ascending=False)
            fig = px.bar(data.sort_values("净支出"), x="净支出", y="category", orientation="h", labels={"category": "", "净支出": "净支出 (RM)"})
            fig.update_xaxes(tickprefix="RM ")
            render_chart(fig, height=max(390, 34 * len(data)))
            st.dataframe(data.rename(columns={"category": "类别"}), hide_index=True, use_container_width=True, column_config={"毛支出": st.column_config.NumberColumn(format="RM %.2f"), "退款": st.column_config.NumberColumn(format="RM %.2f"), "净支出": st.column_config.NumberColumn(format="RM %.2f")})
    else:
        anomalies = analytics.anomaly_transactions(year_all)
        recurring = analytics.recurring_items(year_all)
        l, r = st.columns(2, gap="large")
        with l:
            section_title("异常高额支出")
            if anomalies.empty:
                st.info("没有发现明显异常，或数据量不足。")
            else:
                show = anomalies[["date", "item", "category", "amount"]].head(20).copy(); show["date"] = show["date"].dt.strftime("%Y-%m-%d")
                st.dataframe(show, hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})
        with r:
            section_title("疑似固定／周期支出")
            if recurring.empty:
                st.info("没有发现规律足够明显的周期支出。")
            else:
                show = recurring.head(20).copy(); show["最近日期"] = pd.to_datetime(show["最近日期"]).dt.strftime("%Y-%m-%d"); show["金额波动"] = show["金额波动"].map(lambda v: f"{v:.0%}")
                st.dataframe(show, hide_index=True, use_container_width=True)
        dq = analytics.data_quality(year_all)
        a, b, c, d = st.columns(4)
        a.metric("空项目名称", dq["blank_items"]); b.metric("零或负金额", dq["nonpositive_amounts"]); c.metric("疑似重复", dq["duplicates"]); d.metric("数据库无效记录", len(invalid_rows))


def _render_ai_list_from_plan(plan_dict: dict, transactions: pd.DataFrame) -> None:
    try:
        plan = FinanceQueryPlan.model_validate(plan_dict)
        df = finance_list_frame(plan, transactions)
    except Exception:
        st.session_state.pop("ai_last_list_plan", None)
        return
    section_title("完整本地查询结果")
    st.caption("列表按查询条件本地分页；只保存查询计划，不把整份结果永久塞进 Session。")
    if df.empty:
        st.info("没有匹配记录。"); return
    _, start, end = _page_selector("ai_list_page", len(df), 100)
    show = df.iloc[start:end][["date", "item", "category", "type", "amount", "note"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show["type"] = show["type"].map(TYPE_LABELS)
    st.dataframe(show, hide_index=True, use_container_width=True, height=520, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})
    st.caption(f"共 {len(df):,} 笔；当前显示第 {start + 1:,}–{end:,} 笔。")


def _ai_page(transactions: pd.DataFrame) -> None:
    page_header("AI 洞察", "Gemini 3.7 只负责理解和解释；数字、退款、日期和比较全部由 Python 本地计算并直接显示。")
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
    if not years:
        empty_state("暂无数据可分析"); return
    selected_year = int(st.selectbox("分析年份", years, key="ai_year"))
    signature = ledger_signature(transactions)
    if st.session_state.get("ai_data_signature") != signature or st.session_state.get("ai_scope_year") != selected_year:
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None); st.session_state.pop("ai_last_list_plan", None)
        st.session_state["ai_data_signature"] = signature; st.session_state["ai_scope_year"] = selected_year
    year_expenses = transactions[(transactions["date"].dt.year == selected_year) & (transactions["type"] == EXPENSE)].copy()
    classify, reset, _ = st.columns([1.2, 1, 3])
    if classify.button("AI 宏观归类", type="primary", use_container_width=True):
        try:
            with st.spinner("正在分批归类项目..."):
                mapping = categorize_macro(json.dumps(year_expenses["item"].dropna().unique().tolist(), ensure_ascii=False))
            result = year_expenses.copy(); result["宏观类别"] = result["item"].map(mapping).fillna("其他")
            st.session_state["macro_result"] = result; st.session_state["macro_year"] = selected_year; st.rerun()
        except Exception as exc:
            st.error(f"AI 归类失败：{exc}")
    if reset.button("清除分析", use_container_width=True):
        st.session_state["ai_chat_history"] = []; st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None); st.session_state.pop("ai_last_list_plan", None); st.rerun()
    if st.session_state.get("macro_year") == selected_year and isinstance(st.session_state.get("macro_result"), pd.DataFrame):
        macro = st.session_state["macro_result"].groupby("宏观类别")["amount"].sum().sort_values().reset_index()
        fig = px.bar(macro, x="amount", y="宏观类别", orientation="h", labels={"amount": "支出 (RM)", "宏观类别": ""}); fig.update_xaxes(tickprefix="RM ")
        render_chart(fig, height=420)
    st.divider(); section_title("与账单对话")
    st.caption("每次提问前都会绕过 UI 缓存重新读取最新账本；本地精确结果永远先于 AI 解释。")
    history = st.session_state.setdefault("ai_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]): st.markdown(message["content"])
    question = st.chat_input("例如：8月打油多少钱？有几笔支出？跟上个月比？退款多少？")
    if question:
        try:
            should_rerun_for_list = False
            with st.chat_message("assistant"):
                with st.spinner("正在读取最新账本并本地计算..."):
                    fresh, _, truncated = fetch_transactions_interactive_fresh()
                    if truncated:
                        st.warning("最新账本超过 100,000 笔，AI 交互查询只使用前 100,000 笔；请先归档。")
                    plan = plan_finance_question(question, selected_year, fresh, st.session_state.get("ai_conversation_state"), history)
                    result = execute_finance_plan(plan, fresh)
                    summary = authoritative_summary_markdown(result)
                    explanation = answer_finance_question(question, result)
                st.markdown(summary)
                if explanation:
                    st.caption("AI 解释")
                    st.markdown(explanation)
            history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": summary + ("\n\n" + explanation if explanation else "")}])
            st.session_state["ai_chat_history"] = history[-30:]
            st.session_state["ai_conversation_state"] = state_from_plan(plan, result)
            st.session_state["ai_data_signature"] = ledger_signature(fresh)
            if plan.intent == "list":
                st.session_state["ai_last_list_plan"] = plan.model_dump()
                refresh_data()
                should_rerun_for_list = True
            else:
                st.session_state.pop("ai_last_list_plan", None)
            if should_rerun_for_list:
                st.rerun()
        except Exception as exc:
            st.error(f"AI 查询失败：{exc}")
    if st.session_state.get("ai_last_list_plan"):
        _render_ai_list_from_plan(st.session_state["ai_last_list_plan"], transactions)


def _settings_page(transactions: pd.DataFrame, invalid_rows: pd.DataFrame, categories: list[str]) -> None:
    page_header("设置与备份", "只有选择备份区块并点击准备按钮时才会全量读取 Supabase。")
    section = st.segmented_control("设置区块", ["类别管理", "数据修复", "备份"], default="类别管理", key="settings_section")
    if section == "类别管理":
        registered = {v.casefold() for v in load_category_rows()}
        usage = transactions.groupby("category").agg(使用笔数=("amount", "size"), 累计金额=("amount", "sum")).reset_index().rename(columns={"category": "类别"}) if not transactions.empty else pd.DataFrame(columns=["类别", "使用笔数", "累计金额"])
        if not usage.empty:
            usage["状态"] = usage["类别"].map(lambda v: "已登记" if str(v).casefold() in registered else "历史记录未登记")
        st.dataframe(usage, hide_index=True, use_container_width=True)
        missing = unregistered_categories(transactions)
        if missing:
            st.warning("发现未登记历史类别：" + "、".join(missing))
            if st.button("登记全部未登记类别", use_container_width=True):
                failures = []
                for name in missing:
                    try: create_category(name)
                    except Exception as exc: failures.append(f"{name}: {exc}")
                if failures: st.error("部分登记失败：" + "；".join(failures))
                else: st.toast("已登记全部历史类别"); st.rerun()
        left, right = st.columns(2, gap="large")
        with left:
            section_title("新增类别")
            name = st.text_input("类别名称", key="settings_new_category")
            if st.button("新增类别", type="primary", use_container_width=True):
                try: st.toast("类别已建立" if create_category(name) else "类别已存在"); st.rerun()
                except Exception as exc: st.error(f"新增失败：{exc}")
        with right:
            section_title("改名或合并类别")
            if categories:
                source = st.selectbox("原类别", categories, key="merge_source")
                mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True)
                choices = [v for v in categories if v.casefold() != source.casefold()]
                target = st.selectbox("目标类别", choices, key="merge_target") if mode == "现有类别" and choices else st.text_input("新类别名称", key="merge_new_name")
                confirm = st.checkbox("我确认执行类别合并", key="merge_confirm")
                if st.button("执行改名／合并", disabled=not confirm, use_container_width=True):
                    try:
                        result = merge_category_safely(source, target); st.success(f"完成：移动 {result.moved_rows} 笔交易。" + (" " + result.cleanup_note if result.cleanup_note else "")); st.rerun()
                    except Exception as exc: st.error(f"合并失败：{exc}")
    elif section == "数据修复":
        if invalid_rows.empty:
            st.success("没有发现无效记录。")
        else:
            st.warning(f"发现 {len(invalid_rows)} 笔无效记录；它们不会进入报表。")
            st.dataframe(invalid_rows, hide_index=True, use_container_width=True, height=360)
            row_map = {}
            for _, row in invalid_rows.iterrows():
                try: row_map[int(row["id"])] = row.to_dict()
                except Exception: pass
            if row_map:
                selected = st.selectbox("选择无效记录进行修复", list(row_map), format_func=lambda i: f"ID {i} · {row_map[i].get('item', '')} · {row_map[i].get('issues', '')}")
                if st.button("打开修复表单", type="primary", use_container_width=True): repair_invalid_dialog(row_map[selected])
            st.download_button("下载无效记录 CSV", safe_csv_bytes(invalid_rows), f"WY_Wallet_invalid_{today_my()}.csv", mime="text/csv", use_container_width=True)
    else:
        st.caption("备份不会在普通页面 rerun 时偷偷全量读取数据库。")
        if st.button("准备最新完整备份", type="primary", use_container_width=True):
            try:
                with st.spinner("正在从 Supabase 全量读取最新数据..."):
                    fresh, fresh_invalid, _ = fetch_transactions_fresh(max_rows=None)
                    fresh_categories = fetch_category_rows_fresh()
                    export = fresh.copy()
                    if not export.empty: export["date"] = export["date"].dt.date
                    category_df = pd.DataFrame({"name": fresh_categories})
                    metadata = pd.DataFrame([
                        ["export_time", now_my().isoformat()], ["timezone", TIMEZONE_NAME], ["currency", "MYR"],
                        ["valid_transaction_count", len(export)], ["invalid_transaction_count", len(fresh_invalid)],
                        ["registered_category_count", len(category_df)], ["app_version", APP_VERSION], ["build_id", BUILD_ID],
                    ], columns=["key", "value"])
                    st.session_state["backup_bundle"] = {
                        "excel": build_backup_excel(export, category_df, metadata, fresh_invalid),
                        "csv": safe_csv_bytes(export), "time": now_my().isoformat(timespec="seconds"),
                    }
            except Exception as exc:
                st.error(f"准备备份失败：{exc}")
        bundle = st.session_state.get("backup_bundle")
        if bundle:
            st.success(f"备份已准备：{bundle['time']}")
            d1, d2 = st.columns(2)
            d1.download_button("下载最新完整 Excel 备份", bundle["excel"], f"WY_Wallet_V2_{today_my()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
            d2.download_button("下载最新交易 CSV", bundle["csv"], f"WY_Wallet_V2_{today_my()}.csv", mime="text/csv", use_container_width=True)
            st.caption("Excel/CSV 已防止以 =、+、-、@ 开头的外部文本被 Spreadsheet 当成公式执行。")


def run() -> None:
    protected = _require_optional_private_access()
    st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
    inject_css()
    try:
        transactions = load_transactions(); invalid_rows = load_invalid_transactions(); categories = _sorted_categories(transactions)
    except Exception as exc:
        st.error(f"无法读取 Supabase：{exc}"); st.stop()
    if transactions_truncated():
        st.warning("交易超过网页互动读取上限 100,000 笔；请立即备份或归档。")
    if not invalid_rows.empty:
        st.warning(f"数据库有 {len(invalid_rows)} 笔无效记录，已从统计排除。到「设置与备份 → 数据修复」处理。")
    with st.sidebar:
        st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V2</div></div>', unsafe_allow_html=True)
        if st.button("＋ 新增交易", type="primary", use_container_width=True): add_transaction_dialog(categories)
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", use_container_width=True)
        navigation = st.radio("导航", ["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"], format_func=lambda v: {"总览": "⌂  总览", "交易记录": "≡  交易记录", "分析报表": "▥  分析报表", "AI 洞察": "✦  AI 洞察", "设置与备份": "⚙  设置与备份"}[v], label_visibility="collapsed")
        st.divider()
        if st.button("↻ 刷新数据", use_container_width=True): refresh_data(); st.rerun()
        st.caption(f"数据读取：{data_loaded_at() or '未知'}")
        st.caption(f"{APP_VERSION} · {BUILD_ID}")
        st.caption(f"Malaysia time · {TIMEZONE_NAME}")
        if not protected:
            st.warning("未配置 WEB_ACCESS_PASSWORD。若 Streamlit App 不是平台私有访问，请在 Secrets 加上该密码保护。")
    if navigation == "总览": _dashboard(transactions)
    elif navigation == "交易记录": _transactions_page(transactions, categories)
    elif navigation == "分析报表": _reports_page(transactions, invalid_rows)
    elif navigation == "AI 洞察": _ai_page(transactions)
    else: _settings_page(transactions, invalid_rows, categories)
