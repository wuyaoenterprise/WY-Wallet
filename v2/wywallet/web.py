from __future__ import annotations

import calendar
import html
import io
import json

import pandas as pd
import plotly.express as px
import streamlit as st

from . import analytics
from .ai import answer_finance_question, categorize_macro, execute_finance_plan, plan_finance_question, state_from_plan
from .config import ADD_CATEGORY_OPTION, APP_TITLE, EXPENSE, INCOME, MONTH_LABELS, TIMEZONE_NAME, TYPE_LABELS, now_my, today_my
from .db import (
    create_category, delete_transaction, fetch_category_rows_fresh, fetch_transactions_fresh, insert_transactions,
    ledger_signature, load_categories, load_category_rows, load_invalid_transactions, load_transactions,
    merge_category_safely, normalize_transaction, refresh_data, transactions_truncated, unregistered_categories,
    update_transaction,
)
from .ui import empty_state, inject_css, money, page_header, render_chart, safe_detail_html, section_title


def _sorted_categories(transactions: pd.DataFrame) -> list[str]:
    categories = load_categories(transactions)
    counts = transactions["category"].value_counts().to_dict() if not transactions.empty else {}
    return sorted(categories, key=lambda value: (-counts.get(value, 0), value.casefold()))


def _build_calendar_html(expenses: pd.DataFrame, year: int, month: int) -> str:
    daily = expenses.groupby(expenses["date"].dt.day)["amount"].sum().to_dict() if not expenses.empty else {}
    headers = "".join(f'<div class="wy-calendar-head">{name}</div>' for name in ["一", "二", "三", "四", "五", "六", "日"])
    cells: list[str] = []
    for week in calendar.monthcalendar(int(year), int(month)):
        for day_number in week:
            if day_number == 0:
                cells.append('<div class="wy-calendar-day" style="opacity:.2"></div>')
            else:
                value = float(daily.get(day_number, 0.0))
                amount_html = f'<div class="wy-calendar-amount">{money(value)}</div>' if value else ""
                cells.append(f'<div class="wy-calendar-day"><div class="wy-calendar-date">{day_number}</div>{amount_html}</div>')
    return f'<div class="wy-calendar">{headers}{"".join(cells)}</div>'


def _clamp_page_state(key: str, page_count: int) -> None:
    if key in st.session_state:
        try:
            value = int(st.session_state[key])
        except Exception:
            value = 1
        if value < 1 or value > page_count:
            st.session_state[key] = 1


def _page_selector(key: str, count: int, page_size: int) -> tuple[int, int, int]:
    page_count = max(1, (count + page_size - 1) // page_size)
    _clamp_page_state(key, page_count)
    if page_count > 1:
        page = int(st.selectbox("分页", range(1, page_count + 1), format_func=lambda x: f"第 {x}/{page_count} 页", key=key))
    else:
        page = 1
    start = (page - 1) * page_size
    return page, start, min(start + page_size, count)


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=today_my(), key="add_date")
    tx_type = col_type.segmented_control("类型", options=[EXPENSE, INCOME], default=EXPENSE, format_func=lambda value: TYPE_LABELS[value], key="add_type")
    options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="add_category")
    new_category_name = st.text_input("新类别名称", placeholder="保存后同时登记", key="add_new_category") if selected_category == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="add_item")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="0.00", key="add_amount")
    note = st.text_area("备注（可选）", key="add_note")
    if st.button("保存交易", type="primary", use_container_width=True):
        category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
        payload = {"date": tx_date, "item": item, "category": category, "type": tx_type or EXPENSE, "amount": amount, "note": note}
        try:
            normalized = normalize_transaction(payload)
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
        st.error("找不到这笔有效交易，它可能已被删除或已变成无效数据。")
        return
    row = match.iloc[0]
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=row["date"].date(), key=f"edit_date_{transaction_id}")
    tx_type = c2.segmented_control("类型", [EXPENSE, INCOME], default=row["type"], format_func=lambda v: TYPE_LABELS[v], key=f"edit_type_{transaction_id}")
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
        payload = {"date": tx_date, "item": item, "category": category, "type": tx_type or EXPENSE, "amount": amount, "note": note}
        try:
            normalized = normalize_transaction(payload)
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
        st.error("找不到这笔交易。")
        return
    row = match.iloc[0].to_dict()
    st.warning("删除成功后才会建立撤销快照；若数据库删除失败，不会产生假的撤销记录。")
    st.write(f"**{row['item']}**")
    st.caption(f"{row['date'].date()} · {row['category']} · {money(row['amount'])}")
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
    tx_id = raw_row.get("id")
    try:
        tx_id_int = int(tx_id)
    except Exception:
        st.error("这笔记录的 id 本身无效，无法通过网页安全更新。请先在数据库修复 id。")
        return
    parsed_date = pd.to_datetime(raw_row.get("date"), errors="coerce")
    default_date = today_my() if pd.isna(parsed_date) else parsed_date.date()
    parsed_amount = pd.to_numeric(raw_row.get("amount"), errors="coerce")
    default_amount = float(parsed_amount) if not pd.isna(parsed_amount) and float(parsed_amount) > 0 else 0.01
    st.caption(f"当前问题：{raw_row.get('issues', '')}")
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=default_date, key=f"repair_date_{tx_id_int}")
    raw_type = str(raw_row.get("type") or "")
    tx_type = c2.selectbox("类型", [EXPENSE, INCOME], index=0 if raw_type not in [EXPENSE, INCOME] else [EXPENSE, INCOME].index(raw_type), format_func=lambda v: TYPE_LABELS[v], key=f"repair_type_{tx_id_int}")
    item = st.text_input("项目／商家", value=str(raw_row.get("item") or ""), key=f"repair_item_{tx_id_int}")
    category = st.text_input("类别", value=str(raw_row.get("category") or ""), key=f"repair_cat_{tx_id_int}")
    amount = st.number_input("金额", min_value=0.01, step=0.01, value=default_amount, key=f"repair_amount_{tx_id_int}")
    note = st.text_area("备注", value=str(raw_row.get("note") or ""), key=f"repair_note_{tx_id_int}")
    if st.button("保存修复", type="primary", use_container_width=True):
        try:
            update_transaction(tx_id_int, {"date": tx_date, "item": item, "category": category, "type": tx_type, "amount": amount, "note": note})
            st.toast("无效交易已修复并重新纳入报表")
            st.rerun()
        except Exception as exc:
            st.error(f"修复失败：{exc}")


def _render_transaction_cards(filtered: pd.DataFrame, categories: list[str]) -> None:
    if filtered.empty:
        empty_state("没有符合条件的交易")
        return
    _, start, end = _page_selector("card_page", len(filtered), 30)
    for _, row in filtered.iloc[start:end].iterrows():
        with st.container(border=True):
            top, amount_col = st.columns([3, 1.25])
            top.markdown(f"**{html.escape(str(row['item']))}**")
            top.caption(f"{row['date'].date()} · {row['category']} · {TYPE_LABELS.get(row['type'], row['type'])}")
            amount_col.markdown(f"### {'+' if row['type'] == INCOME else '−'}{money(row['amount'])}")
            if row.get("note"):
                st.caption(str(row["note"]))
            e, d, _ = st.columns([1, 1, 3])
            if e.button("编辑", key=f"card_edit_{int(row['id'])}", use_container_width=True):
                edit_transaction_dialog(int(row["id"]), categories)
            if d.button("删除", key=f"card_delete_{int(row['id'])}", use_container_width=True):
                delete_transaction_dialog(int(row["id"]))


def _render_static_transaction_table(filtered: pd.DataFrame, categories: list[str]) -> None:
    if filtered.empty:
        empty_state("没有符合条件的交易")
        return
    _, start, end = _page_selector("table_page", len(filtered), 40)
    page = filtered.iloc[start:end].copy()
    display = pd.DataFrame({
        "日期": page["date"].dt.strftime("%Y-%m-%d"), "项目": page["item"], "类别": page["category"],
        "类型": page["type"].map(TYPE_LABELS),
        "金额": page.apply(lambda r: ("+" if r["type"] == INCOME else "−") + money(r["amount"]), axis=1),
        "备注": page["note"],
    })
    st.caption("此表格为静态分页表，不提供栏头排序；显示顺序始终与上方排序条件一致。")
    st.table(display)
    row_map = {int(row["id"]): row for _, row in page.iterrows()}
    selected_id = st.selectbox(
        "选择一笔交易进行操作", [None] + list(row_map),
        format_func=lambda value: "— 请选择 —" if value is None else f"{row_map[value]['date'].date()} · {row_map[value]['item']} · {money(row_map[value]['amount'])}",
        key="table_action_id",
    )
    if selected_id is not None:
        row = row_map[int(selected_id)]
        st.markdown(safe_detail_html(row["item"], row["category"], TYPE_LABELS[row["type"]], row["amount"], str(row["date"].date()), row["note"], row["type"] == INCOME), unsafe_allow_html=True)
        e, d, _ = st.columns([1, 1, 4])
        if e.button("编辑交易", type="primary", use_container_width=True):
            edit_transaction_dialog(int(selected_id), categories)
        if d.button("删除交易", use_container_width=True):
            delete_transaction_dialog(int(selected_id))


def _dashboard(transactions: pd.DataFrame) -> None:
    page_header("财务总览", "用最少时间了解本月状况、近期趋势和主要支出。")
    now = now_my()
    current = analytics.month_slice(transactions, now.year, now.month)
    py, pm = analytics.previous_month(now.year, now.month)
    previous = analytics.month_slice(transactions, py, pm)
    income, expense, balance = analytics.calculate_totals(current)
    _, prior_expense, _ = analytics.calculate_totals(previous)
    change = None if prior_expense == 0 else (expense - prior_expense) / prior_expense
    days = calendar.monthrange(now.year, now.month)[1]
    projected = expense / max(now.day, 1) * days
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("本月收入", money(income))
    m2.metric("本月支出", money(expense), "无上月数据" if change is None else f"{change:+.1%} 对比上月", delta_color="inverse")
    m3.metric("本月结余", money(balance))
    m4.metric("日均支出", money(expense / max(now.day, 1)))
    m5.metric("月底预计", money(projected), "按当前速度", delta_color="off")
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        section_title("最近 12 个月支出")
        twelve = analytics.recent_months_summary(transactions)
        fig = px.bar(twelve, x="月份", y="支出", text_auto=".0f")
        fig.update_yaxes(rangemode="tozero", tickprefix="RM ")
        fig.update_traces(hovertemplate="%{x}<br>支出 RM %{y:,.2f}<extra></extra>")
        render_chart(fig, height=335)
    with right:
        section_title("本月支出类别")
        expenses = current[current["type"] == EXPENSE]
        summary = expenses.groupby("category")["amount"].sum().sort_values(ascending=False).head(7)
        if summary.empty:
            empty_state("本月暂无支出")
        else:
            for category, value in summary.items():
                label, val = st.columns([1.6, 1])
                label.write(f"**{category}**")
                val.write(money(value))
                st.progress(min(float(value / expense), 1.0) if expense else 0.0)
    section_title("最近交易")
    if transactions.empty:
        empty_state("还没有任何交易记录")
    else:
        recent = transactions.head(8).copy()
        recent["日期"] = recent["date"].dt.strftime("%Y-%m-%d")
        recent["项目"] = recent["item"]
        recent["类别"] = recent["category"]
        recent["类型"] = recent["type"].map(TYPE_LABELS)
        recent["金额"] = recent.apply(lambda r: r["amount"] if r["type"] == INCOME else -r["amount"], axis=1)
        st.dataframe(recent[["日期", "项目", "类别", "类型", "金额"]], hide_index=True, use_container_width=True, height=315, column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})


def _transactions_page(transactions: pd.DataFrame, categories: list[str]) -> None:
    page_header("交易记录", "稳定排序、字面搜索与可编辑账目；表格模式不会被栏头临时排序干扰。")
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
        refresh_data()
        st.rerun()
    with st.expander("筛选交易", expanded=True):
        s, y, m = st.columns([2, 1, 1])
        search = s.text_input("搜索", placeholder="项目、类别或备注")
        years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        year = y.selectbox("年份", ["全部"] + years)
        month = m.selectbox("月份", ["全部"] + list(range(1, 13)))
        t, c, so = st.columns([1, 1.5, 1.5])
        tx_type = t.selectbox("类型", ["全部", EXPENSE, INCOME], format_func=lambda x: "全部" if x == "全部" else TYPE_LABELS[x])
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
        filtered = filtered[filtered["category"] == category]
    rules = {"日期：最新优先": ("date", False), "日期：最早优先": ("date", True), "金额：由高到低": ("amount", False), "金额：由低到高": ("amount", True)}
    col, asc = rules[sort]
    filtered = filtered.sort_values([col, "id"], ascending=[asc, asc]).reset_index(drop=True)
    fi, fe, fb = analytics.calculate_totals(filtered)
    a, b, c, d = st.columns(4)
    a.metric("筛选结果", f"{len(filtered):,} 笔")
    b.metric("支出", money(fe))
    c.metric("收入", money(fi))
    d.metric("净额", money(fb))
    view = st.segmented_control("显示方式", ["表格", "卡片"], default="表格", key="transaction_view")
    if view == "卡片":
        _render_transaction_cards(filtered, categories)
    else:
        _render_static_transaction_table(filtered, categories)


def _reports_page(transactions: pd.DataFrame, invalid_rows: pd.DataFrame) -> None:
    page_header("分析报表", "年度、月度、类别与消费规律；金额全部由本地数据计算。")
    if transactions.empty:
        empty_state("暂无有效数据可分析")
        return
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
    now = now_my()
    default = years.index(now.year) if now.year in years else 0
    year = int(st.selectbox("分析年份", years, index=default))
    annual = analytics.monthly_summary(transactions, year)
    year_all = transactions[transactions["date"].dt.year == year].copy()
    expenses = year_all[year_all["type"] == EXPENSE].copy()
    annual_expense = float(annual["支出"].sum())
    annual_income = float(annual["收入"].sum())
    monthly_avg = analytics.average_monthly_expense(annual, year)
    savings = analytics.annual_savings_rate(annual)
    yoy = analytics.same_period_yoy(transactions, year)
    elapsed = analytics.elapsed_month_count(year)
    scope = annual[annual["month"] <= elapsed] if elapsed else annual
    highest = scope.loc[scope["支出"].idxmax()] if not scope.empty else annual.iloc[0]
    avg_label = "截至目前月均" if year == now.year else "全年月均"
    yoy_text = "无同期数据" if not yoy or yoy["change"] is None else f"{yoy['change']:+.1%} 同期同比"
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("年度支出", money(annual_expense), yoy_text, delta_color="inverse")
    m2.metric("年度收入", money(annual_income))
    m3.metric(avg_label, "N/A" if monthly_avg is None else money(monthly_avg))
    m4.metric("储蓄率", "N/A" if savings is None else f"{savings:.1f}%")
    m5.metric("最高月份", str(highest["月份"]), money(highest["支出"]), delta_color="off")
    if yoy:
        st.caption(f"同比口径：{yoy['current_start']}–{yoy['current_end']} 对比 {yoy['previous_start']}–{yoy['previous_end']}。")
    quick, annual_tab, monthly_tab, category_tab, insight_tab = st.tabs(["快速总览", "年度趋势", "月度明细", "类别分析", "异常与规律"])
    with quick:
        l, r = st.columns([1.55, 1], gap="large")
        with l:
            section_title("全年每月支出")
            f = px.bar(annual, x="月份", y="支出", text_auto=".0f")
            if monthly_avg is not None:
                f.add_hline(y=monthly_avg, line_dash="dash", annotation_text=f"月均 {money(monthly_avg)}")
            f.update_yaxes(rangemode="tozero", tickprefix="RM ")
            render_chart(f, height=390)
        with r:
            section_title("年度类别占比")
            cat = expenses.groupby("category")["amount"].sum().sort_values(ascending=False).reset_index()
            if cat.empty:
                st.info("没有支出数据。")
            else:
                top = cat.head(7).copy()
                if len(cat) > 7:
                    top = pd.concat([top, pd.DataFrame({"category": ["其他类别"], "amount": [cat.iloc[7:]["amount"].sum()]})], ignore_index=True)
                f = px.pie(top, values="amount", names="category", hole=.56)
                f.update_traces(textposition="inside", textinfo="percent", hovertemplate="%{label}<br>RM %{value:,.2f}<br>%{percent}<extra></extra>")
                render_chart(f, height=390, legend=True, hovermode="closest")
        selector, _ = st.columns([1, 3])
        qmonth = int(selector.selectbox("快速查看月份", range(1, 13), index=min(now.month - 1, 11), format_func=lambda v: f"{v}月", key="quick_month"))
        selected = analytics.month_slice(transactions, year, qmonth)
        selected_expenses = selected[selected["type"] == EXPENSE]
        days = calendar.monthrange(year, qmonth)[1]
        daily = pd.DataFrame({"day": range(1, days + 1)})
        if not selected_expenses.empty:
            grouped = selected_expenses.assign(day=selected_expenses["date"].dt.day).groupby("day")["amount"].sum().reset_index()
            daily = daily.merge(grouped, on="day", how="left")
        daily["amount"] = daily.get("amount", pd.Series(0.0, index=daily.index)).fillna(0.0)
        l, r = st.columns([1.45, 1], gap="large")
        with l:
            section_title("每日支出")
            f = px.bar(daily, x="day", y="amount", labels={"day": "日期", "amount": "支出 (RM)"})
            f.update_xaxes(dtick=1)
            f.update_yaxes(rangemode="tozero", tickprefix="RM ")
            render_chart(f, height=355)
        with r:
            section_title("当月类别排行")
            mcat = selected_expenses.groupby("category")["amount"].sum().sort_values().reset_index()
            if mcat.empty:
                st.info("该月没有支出。")
            else:
                f = px.bar(mcat, x="amount", y="category", orientation="h", labels={"amount": "支出 (RM)", "category": ""})
                f.update_xaxes(rangemode="tozero", tickprefix="RM ")
                render_chart(f, height=355, hovermode="closest")
        with st.expander("查看月历"):
            st.markdown(_build_calendar_html(selected_expenses, year, qmonth), unsafe_allow_html=True)
    with annual_tab:
        section_title("收入、支出与结余")
        cash = annual.melt(id_vars=["month", "月份"], value_vars=["收入", "支出", "结余"], var_name="指标", value_name="金额")
        f = px.line(cash, x="月份", y="金额", color="指标", markers=True)
        f.update_yaxes(tickprefix="RM ")
        render_chart(f, height=390, legend=True)
        prior = analytics.monthly_summary(transactions, year - 1)
        l, r = st.columns(2, gap="large")
        with l:
            section_title("累计支出：本年 vs 上年")
            compare = annual[["月份", "累计支出"]].rename(columns={"累计支出": str(year)}).copy()
            compare[str(year - 1)] = prior["累计支出"].values
            melt = compare.melt(id_vars="月份", var_name="年份", value_name="累计支出")
            f = px.line(melt, x="月份", y="累计支出", color="年份", markers=True)
            f.update_yaxes(rangemode="tozero", tickprefix="RM ")
            render_chart(f, height=350, legend=True)
        with r:
            section_title("每月储蓄率")
            f = px.bar(annual, x="月份", y="储蓄率", text_auto=".0f")
            f.add_hline(y=0)
            f.update_yaxes(ticksuffix="%")
            render_chart(f, height=350)
        section_title("类别随月份变化")
        if expenses.empty:
            st.info("没有支出数据。")
        else:
            stacked = expenses.assign(月份=expenses["date"].dt.month.map(lambda x: f"{x}月")).groupby(["月份", "category"])["amount"].sum().reset_index()
            stacked["月份"] = pd.Categorical(stacked["月份"], categories=MONTH_LABELS, ordered=True)
            stacked = stacked.sort_values("月份")
            f = px.bar(stacked, x="月份", y="amount", color="category", labels={"amount": "支出 (RM)", "category": "类别"})
            f.update_yaxes(rangemode="tozero", tickprefix="RM ")
            render_chart(f, height=430, legend=True)
    with monthly_tab:
        selector, _ = st.columns([1, 3])
        month = int(selector.selectbox("选择月份", range(1, 13), index=min(now.month - 1, 11), format_func=lambda v: f"{v}月", key="report_month"))
        selected = analytics.month_slice(transactions, year, month)
        mi, me, mb = analytics.calculate_totals(selected)
        expense_rows = selected[selected["type"] == EXPENSE].copy()
        days = calendar.monthrange(year, month)[1]
        current_month = year == now.year and month == now.month
        past_month = year < now.year or (year == now.year and month < now.month)
        projected = me / max(now.day, 1) * days if current_month else (me if past_month else None)
        fifth_label = "月底预计" if current_month or not past_month else "实际支出"
        fifth_delta = "按当前速度" if current_month else ("实际" if past_month else "未来月份")
        mm1, mm2, mm3, mm4, mm5 = st.columns(5)
        mm1.metric("收入", money(mi))
        mm2.metric("支出", money(me))
        mm3.metric("结余", money(mb))
        mm4.metric("交易笔数", f"{len(selected):,}")
        mm5.metric(fifth_label, "N/A" if projected is None else money(projected), fifth_delta, delta_color="off")
        if expense_rows.empty:
            st.info("该月没有支出。")
        else:
            expense_rows["day"] = expense_rows["date"].dt.day
            daily_stacked = expense_rows.groupby(["day", "category"])["amount"].sum().reset_index()
            section_title("每日支出及类别组成")
            f = px.bar(daily_stacked, x="day", y="amount", color="category", labels={"day": "日期", "amount": "支出 (RM)", "category": "类别"})
            f.update_xaxes(dtick=1)
            f.update_yaxes(rangemode="tozero", tickprefix="RM ")
            render_chart(f, height=420, legend=True)
            l, r = st.columns(2, gap="large")
            with l:
                section_title("平均每个星期几的支出")
                wd = analytics.weekday_average(expense_rows, year, month)
                f = px.bar(wd, x="星期", y="平均每个该星期", labels={"平均每个该星期": "平均支出 (RM)"})
                f.update_yaxes(rangemode="tozero", tickprefix="RM ")
                render_chart(f, height=350)
            with r:
                section_title("单笔金额分布")
                f = px.histogram(expense_rows, x="amount", nbins=min(16, max(5, len(expense_rows) // 3)), labels={"amount": "单笔金额 (RM)", "count": "笔数"})
                f.update_xaxes(rangemode="tozero", tickprefix="RM ")
                render_chart(f, height=350, hovermode="closest")
            with st.expander("查看月历"):
                st.markdown(_build_calendar_html(expense_rows, year, month), unsafe_allow_html=True)
    with category_tab:
        if expenses.empty:
            st.info("该年度没有支出。")
        else:
            data = expenses.groupby("category")["amount"].agg(["sum", "size", "mean"]).reset_index().rename(columns={"sum": "总支出", "size": "次数", "mean": "平均每笔"}).sort_values("总支出", ascending=False)
            l, r = st.columns([1.25, 1], gap="large")
            with l:
                section_title("类别金额排行")
                h = data.sort_values("总支出")
                f = px.bar(h, x="总支出", y="category", orientation="h", labels={"category": "", "总支出": "支出 (RM)"})
                f.update_xaxes(rangemode="tozero", tickprefix="RM ")
                render_chart(f, height=max(390, 34 * len(h)), hovermode="closest")
            with r:
                section_title("类别详细指标")
                st.dataframe(data.rename(columns={"category": "类别"}), hide_index=True, use_container_width=True, height=430, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
            chosen = st.selectbox("深入查看类别", data["category"].tolist())
            rows = expenses[expenses["category"] == chosen].copy()
            monthly = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS})
            grouped = rows.assign(month=rows["date"].dt.month).groupby("month")["amount"].sum().reset_index(name="支出")
            monthly = monthly.merge(grouped, on="month", how="left").fillna(0)
            l, r = st.columns([1.4, 1], gap="large")
            with l:
                section_title(f"{chosen} 的 12 个月趋势")
                f = px.line(monthly, x="月份", y="支出", markers=True)
                f.update_yaxes(rangemode="tozero", tickprefix="RM ")
                render_chart(f, height=340)
            with r:
                section_title("该类别项目排行")
                rank = rows.groupby("item")["amount"].agg(["sum", "size"]).reset_index().rename(columns={"item": "项目", "sum": "总支出", "size": "次数"}).sort_values("总支出", ascending=False).head(12)
                st.dataframe(rank, hide_index=True, use_container_width=True, height=340, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f")})
    with insight_tab:
        section_title("快速结论")
        if expenses.empty:
            st.info("没有支出数据。")
        else:
            total = float(expenses["amount"].sum())
            by_cat = expenses.groupby("category")["amount"].sum().sort_values(ascending=False)
            if not by_cat.empty and total > 0:
                st.markdown(f'<div class="wy-callout">最大类别是 {html.escape(str(by_cat.index[0]))}，占全年支出的 {by_cat.iloc[0] / total:.1%}。</div>', unsafe_allow_html=True)
        anomalies = analytics.anomaly_transactions(expenses)
        recurring = analytics.recurring_items(expenses)
        l, r = st.columns(2, gap="large")
        with l:
            section_title("异常高额交易")
            st.caption("按类别内部比较，避免用房租和饮料直接互比。")
            if anomalies.empty:
                st.info("没有发现明显异常，或数据量不足。")
            else:
                show = anomalies[["date", "item", "category", "amount"]].head(20).copy()
                show["date"] = show["date"].dt.strftime("%Y-%m-%d")
                show.columns = ["日期", "项目", "类别", "金额"]
                st.dataframe(show, hide_index=True, use_container_width=True, height=420, column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})
        with r:
            section_title("疑似固定／周期支出")
            st.caption("综合月份覆盖、金额稳定性和时间间隔。")
            if recurring.empty:
                st.info("没有发现规律足够明显的周期支出。")
            else:
                show = recurring.head(20).copy()
                show["最近日期"] = pd.to_datetime(show["最近日期"]).dt.strftime("%Y-%m-%d")
                show["金额波动"] = show["金额波动"].map(lambda v: f"{v:.0%}")
                st.dataframe(show, hide_index=True, use_container_width=True, height=420, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
        dq = analytics.data_quality(year_all)
        section_title("数据质量检查")
        a, b, c, d = st.columns(4)
        a.metric("空项目名称", dq["blank_items"])
        b.metric("零或负金额", dq["nonpositive_amounts"])
        c.metric("疑似重复记录", dq["duplicates"])
        d.metric("数据库无效记录", len(invalid_rows))


def _ai_page(transactions: pd.DataFrame) -> None:
    page_header("AI 洞察", "Gemini 3.7 Flash 负责理解与解释；金额、日期范围与比较全部由本地账本计算。")
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
    if not years:
        empty_state("暂无数据可分析")
        return
    selected_year = int(st.selectbox("分析年份", years, key="ai_year"))
    signature = ledger_signature(transactions)
    if st.session_state.get("ai_data_signature") != signature or st.session_state.get("ai_scope_year") != selected_year:
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None)
        st.session_state.pop("ai_last_list_rows", None)
        st.session_state["ai_data_signature"] = signature
        st.session_state["ai_scope_year"] = selected_year
    year_expenses = transactions[(transactions["date"].dt.year == selected_year) & (transactions["type"] == EXPENSE)].copy()
    classify, reset, _ = st.columns([1.2, 1, 3])
    if classify.button("AI 宏观归类", type="primary", use_container_width=True):
        try:
            with st.spinner("正在分批归类项目..."):
                mapping = categorize_macro(json.dumps(year_expenses["item"].dropna().unique().tolist(), ensure_ascii=False))
            result = year_expenses.copy()
            result["宏观类别"] = result["item"].map(mapping).fillna("其他")
            st.session_state["macro_result"] = result
            st.session_state["macro_year"] = selected_year
            st.rerun()
        except Exception as exc:
            st.error(f"AI 归类失败：{exc}")
    if reset.button("清除分析", use_container_width=True):
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None)
        st.session_state.pop("ai_last_list_rows", None)
        st.rerun()
    if st.session_state.get("macro_year") == selected_year and isinstance(st.session_state.get("macro_result"), pd.DataFrame):
        macro = st.session_state["macro_result"].groupby("宏观类别")["amount"].sum().sort_values().reset_index()
        f = px.bar(macro, x="amount", y="宏观类别", orientation="h", labels={"amount": "支出 (RM)", "宏观类别": ""})
        f.update_xaxes(rangemode="tozero", tickprefix="RM ")
        render_chart(f, height=420, hovermode="closest")
    st.divider()
    section_title("与账单对话")
    st.caption("AI 只规划查询；Python 本地执行精确日期范围、金额、同比／环比和完整明细。")
    history = st.session_state.setdefault("ai_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    question = st.chat_input("例如：8月打油多少？1到8月分别多少？跟上一段比呢？那2025呢？")
    if question:
        history.append({"role": "user", "content": question})
        try:
            with st.chat_message("assistant"):
                with st.spinner("正在解析问题并本地查账..."):
                    plan = plan_finance_question(question, selected_year, transactions, st.session_state.get("ai_conversation_state"), history)
                    result = execute_finance_plan(plan, transactions)
                    reply = answer_finance_question(question, result)
                st.markdown(reply)
            history.append({"role": "assistant", "content": reply})
            st.session_state["ai_conversation_state"] = state_from_plan(plan, result)
            if plan.intent == "list":
                st.session_state["ai_last_list_rows"] = result.get("ui_transactions", [])
                st.session_state["ai_last_list_title"] = question
        except Exception as exc:
            st.error(f"AI 查询规划失败：{exc}")
    rows = st.session_state.get("ai_last_list_rows")
    if rows is not None:
        section_title("完整本地查询结果")
        st.caption(f"对应问题：{st.session_state.get('ai_last_list_title', '')}。此表来自本地数据库查询，不受 AI 输出长度限制。")
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("没有匹配记录。")
        else:
            st.dataframe(df, hide_index=True, use_container_width=True, height=520, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})


def _settings_page(transactions: pd.DataFrame, invalid_rows: pd.DataFrame, categories: list[str]) -> None:
    page_header("设置与备份", "管理类别、修复无效记录，并下载刚刚从 Supabase 重新读取的完整备份。")
    category_tab, quality_tab, backup_tab = st.tabs(["类别管理", "数据修复", "备份"])
    with category_tab:
        registered = {v.casefold() for v in load_category_rows()}
        usage = transactions.groupby("category").agg(使用笔数=("amount", "size"), 累计金额=("amount", "sum")).reset_index().rename(columns={"category": "类别"}) if not transactions.empty else pd.DataFrame(columns=["类别", "使用笔数", "累计金额"])
        if not usage.empty:
            usage["状态"] = usage["类别"].map(lambda v: "已登记" if str(v).casefold() in registered else "历史记录未登记")
        st.dataframe(usage, hide_index=True, use_container_width=True, column_config={"累计金额": st.column_config.NumberColumn(format="RM %.2f")})
        missing = unregistered_categories(transactions)
        if missing:
            st.warning("发现未登记历史类别：" + "、".join(missing))
            if st.button("登记全部未登记类别", use_container_width=True):
                failures = []
                for name in missing:
                    try:
                        create_category(name)
                    except Exception as exc:
                        failures.append(f"{name}: {exc}")
                if failures:
                    st.error("部分登记失败：" + "；".join(failures))
                else:
                    st.toast("已登记全部历史类别")
                    st.rerun()
        left, right = st.columns(2, gap="large")
        with left:
            section_title("新增类别")
            name = st.text_input("类别名称", key="settings_new_category")
            if st.button("新增类别", type="primary", use_container_width=True):
                try:
                    created = create_category(name)
                    st.toast("类别已建立" if created else "类别已存在")
                    st.rerun()
                except Exception as exc:
                    st.error(f"新增失败：{exc}")
        with right:
            section_title("改名或合并类别")
            if len(categories) < 1:
                st.info("暂无类别。")
            else:
                source = st.selectbox("原类别", categories, key="merge_source")
                mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True)
                choices = [v for v in categories if v.casefold() != source.casefold()]
                target = st.selectbox("目标类别", choices, key="merge_target") if mode == "现有类别" and choices else st.text_input("新类别名称", key="merge_new_name")
                source_count = int((transactions["category"] == source).sum()) if not transactions.empty else 0
                st.caption(f"将移动 {source_count} 笔交易。失败时不会删除任何交易，并会尝试清理空目标类别。")
                confirm = st.checkbox("我确认执行类别合并", key="merge_confirm")
                if st.button("执行改名／合并", disabled=not confirm, use_container_width=True):
                    try:
                        result = merge_category_safely(source, target)
                        st.success(f"完成：移动 {result.moved_rows} 笔交易。" + (" " + result.cleanup_note if result.cleanup_note else ""))
                        st.rerun()
                    except Exception as exc:
                        st.error(f"合并失败：{exc}")
    with quality_tab:
        section_title("数据库无效记录")
        if invalid_rows.empty:
            st.success("没有发现被隐藏或静默修正的无效记录。")
        else:
            st.warning(f"发现 {len(invalid_rows)} 笔无效记录。它们不会被错误当成 Expense，也不会进入报表，直到修复。")
            st.dataframe(invalid_rows, hide_index=True, use_container_width=True, height=360)
            row_map = {}
            for _, row in invalid_rows.iterrows():
                try:
                    row_map[int(row["id"])] = row.to_dict()
                except Exception:
                    continue
            ids = list(row_map)
            if ids:
                selected = st.selectbox("选择无效记录进行修复", ids, format_func=lambda i: f"ID {i} · {row_map[i].get('item', '')} · {row_map[i].get('issues', '')}")
                if st.button("打开修复表单", type="primary", use_container_width=True):
                    repair_invalid_dialog(row_map[selected])
            st.download_button("下载无效记录 CSV", invalid_rows.to_csv(index=False).encode("utf-8-sig"), f"WY_Wallet_invalid_{today_my()}.csv", mime="text/csv", use_container_width=True)
    with backup_tab:
        with st.spinner("正在从 Supabase 重新读取最新数据..."):
            fresh, fresh_invalid, _ = fetch_transactions_fresh()
            fresh_categories = fetch_category_rows_fresh()
        if fresh.empty and fresh_invalid.empty:
            st.info("暂无数据可备份。")
        else:
            export = fresh.copy()
            if not export.empty:
                export["date"] = export["date"].dt.date
            registered_categories = pd.DataFrame({"name": fresh_categories})
            metadata = pd.DataFrame([
                ["export_time", now_my().isoformat()], ["timezone", TIMEZONE_NAME], ["currency", "MYR"],
                ["valid_transaction_count", len(export)], ["invalid_transaction_count", len(fresh_invalid)],
                ["registered_category_count", len(registered_categories)], ["app_version", "V2-stable-2026-09"],
            ], columns=["key", "value"])
            excel = io.BytesIO()
            with pd.ExcelWriter(excel, engine="xlsxwriter") as writer:
                export.to_excel(writer, index=False, sheet_name="Transactions")
                registered_categories.to_excel(writer, index=False, sheet_name="Categories")
                metadata.to_excel(writer, index=False, sheet_name="Metadata")
                fresh_invalid.to_excel(writer, index=False, sheet_name="InvalidRows")
            d1, d2 = st.columns(2)
            d1.download_button("下载最新完整 Excel 备份", excel.getvalue(), f"WY_Wallet_V2_{today_my()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
            d2.download_button("下载最新交易 CSV", export.to_csv(index=False).encode("utf-8-sig"), f"WY_Wallet_V2_{today_my()}.csv", mime="text/csv", use_container_width=True)
            st.caption("每次打开此备份页都会绕过 120 秒 UI 缓存重新读取 Supabase；Excel 还包含 Categories、Metadata、InvalidRows。")


def run() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
    inject_css()
    try:
        transactions = load_transactions()
        invalid_rows = load_invalid_transactions()
        categories = _sorted_categories(transactions)
    except Exception as exc:
        st.error(f"无法读取 Supabase：{exc}")
        st.stop()
    if transactions_truncated():
        st.warning("交易已达到网页安全读取上限 100,000 笔，主界面可能不是完整账本。请立即备份或归档；备份页会执行不设此上限的最新读取。")
    if not invalid_rows.empty:
        st.warning(f"数据库有 {len(invalid_rows)} 笔无效记录，已从统计中排除而不是错误修正。可到「设置与备份 → 数据修复」处理。")
    with st.sidebar:
        st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V2</div></div>', unsafe_allow_html=True)
        if st.button("＋ 新增交易", type="primary", use_container_width=True):
            add_transaction_dialog(categories)
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", use_container_width=True)
        navigation = st.radio("导航", ["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"], format_func=lambda v: {"总览": "⌂  总览", "交易记录": "≡  交易记录", "分析报表": "▥  分析报表", "AI 洞察": "✦  AI 洞察", "设置与备份": "⚙  设置与备份"}[v], label_visibility="collapsed")
        st.divider()
        if st.button("↻ 刷新数据", use_container_width=True):
            refresh_data()
            st.rerun()
        st.caption(f"Malaysia time · {TIMEZONE_NAME}")
        st.caption("V2 与旧网页共用现有 Supabase 数据")
    if navigation == "总览":
        _dashboard(transactions)
    elif navigation == "交易记录":
        _transactions_page(transactions, categories)
    elif navigation == "分析报表":
        _reports_page(transactions, invalid_rows)
    elif navigation == "AI 洞察":
        _ai_page(transactions)
    else:
        _settings_page(transactions, invalid_rows, categories)
