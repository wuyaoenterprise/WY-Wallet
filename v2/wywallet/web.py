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
from .db import create_category, delete_transaction, insert_transactions, load_categories, load_category_rows, load_transactions, merge_category_safely, refresh_data, unregistered_categories, update_transaction
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


def _save_new_transaction(row: dict) -> bool:
    try:
        insert_transactions([row])
        st.toast("交易已保存")
        return True
    except Exception as exc:
        st.error(f"保存失败：{exc}")
        return False


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=today_my(), key="add_date")
    tx_type = col_type.segmented_control("类型", options=[EXPENSE, INCOME], default=EXPENSE, format_func=lambda value: TYPE_LABELS[value], key="add_type")
    options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="add_category")
    new_category_name = st.text_input("新类别名称", placeholder="保存时同时建立", key="add_new_category") if selected_category == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="add_item")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="0.00", key="add_amount")
    note = st.text_area("备注（可选）", key="add_note")
    if st.button("保存交易", type="primary", use_container_width=True):
        effective_category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
        try:
            if selected_category == ADD_CATEGORY_OPTION and effective_category:
                create_category(effective_category)
            if _save_new_transaction({"date": tx_date, "item": item, "category": effective_category, "type": tx_type or EXPENSE, "amount": amount, "note": note}):
                st.rerun()
        except Exception as exc:
            st.error(f"保存失败：{exc}")


@st.dialog("编辑交易", width="large")
def edit_transaction_dialog(transaction_id: int, categories: list[str]) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易，它可能已被删除。")
        return
    row = match.iloc[0]
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=row["date"].date(), key=f"edit_date_{transaction_id}")
    tx_type = col_type.segmented_control("类型", options=[EXPENSE, INCOME], default=row["type"], format_func=lambda value: TYPE_LABELS[value], key=f"edit_type_{transaction_id}")
    options = categories.copy()
    if row["category"] not in options:
        options.insert(0, row["category"])
    options.append(ADD_CATEGORY_OPTION)
    selected_category = st.selectbox("类别", options, index=options.index(row["category"]), key=f"edit_category_{transaction_id}")
    new_category = st.text_input("新类别名称", key=f"edit_new_category_{transaction_id}") if selected_category == ADD_CATEGORY_OPTION else ""
    item = st.text_input("项目或商家", value=row["item"], key=f"edit_item_{transaction_id}")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=float(row["amount"]), key=f"edit_amount_{transaction_id}")
    note = st.text_area("备注", value=row["note"], key=f"edit_note_{transaction_id}")
    if st.button("保存修改", type="primary", use_container_width=True):
        category = new_category.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
        try:
            if selected_category == ADD_CATEGORY_OPTION and category:
                create_category(category)
            update_transaction(transaction_id, {"date": tx_date, "item": item, "category": category, "type": tx_type or EXPENSE, "amount": amount, "note": note})
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
    st.warning("删除后会从 Supabase 移除；本次浏览期间可撤销最近一次删除。")
    st.write(f"**{row['item']}**")
    st.caption(f"{row['date'].date()} · {row['category']} · {money(row['amount'])}")
    confirm = st.checkbox("我确认删除这笔交易", key=f"confirm_delete_{transaction_id}")
    if st.button("确认删除", type="primary", disabled=not confirm, use_container_width=True, key=f"delete_{transaction_id}"):
        try:
            st.session_state["recently_deleted"] = {k: row.get(k) for k in ["date", "item", "category", "type", "amount", "note"]}
            delete_transaction(transaction_id)
            st.toast("交易已删除")
            st.rerun()
        except Exception as exc:
            st.error(f"删除失败：{exc}")


def _render_transaction_cards(filtered: pd.DataFrame, categories: list[str]) -> None:
    if filtered.empty:
        empty_state("没有符合条件的交易")
        return
    page_size = 30
    page_count = max(1, (len(filtered) + page_size - 1) // page_size)
    page = st.selectbox("卡片页", range(1, page_count + 1), format_func=lambda value: f"第 {value}/{page_count} 页", key="card_page") if page_count > 1 else 1
    start = (int(page) - 1) * page_size
    for _, row in filtered.iloc[start:start + page_size].iterrows():
        with st.container(border=True):
            top, amount_col = st.columns([3, 1.25])
            top.markdown(f"**{html.escape(str(row['item']))}**")
            top.caption(f"{row['date'].date()} · {row['category']} · {TYPE_LABELS.get(row['type'], row['type'])}")
            sign = "+" if row["type"] == INCOME else "−"
            amount_col.markdown(f"### {sign}{money(row['amount'])}")
            if row.get("note"):
                st.caption(str(row["note"]))
            edit_col, delete_col, _ = st.columns([1, 1, 3])
            if edit_col.button("编辑", key=f"card_edit_{int(row['id'])}", use_container_width=True):
                edit_transaction_dialog(int(row["id"]), categories)
            if delete_col.button("删除", key=f"card_delete_{int(row['id'])}", use_container_width=True):
                delete_transaction_dialog(int(row["id"]))


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
                val.write(f"<div style='text-align:right'>{money(value)}</div>", unsafe_allow_html=True)
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
    page_header("交易记录", "搜索、筛选、检查与编辑所有账目；手机可切换卡片视图。")
    action_add, action_receipt, action_undo, action_refresh, _ = st.columns([1, 1.2, 1.25, 1, 2.8])
    if action_add.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    with action_receipt:
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", use_container_width=True)
    if st.session_state.get("recently_deleted") and action_undo.button("↩ 撤销删除", use_container_width=True):
        try:
            insert_transactions([st.session_state.pop("recently_deleted")])
            st.toast("已恢复最近删除")
            st.rerun()
        except Exception as exc:
            st.error(f"撤销失败：{exc}")
    if action_refresh.button("↻ 刷新", use_container_width=True):
        refresh_data(); st.rerun()

    with st.expander("筛选交易", expanded=True):
        search_col, year_col, month_col = st.columns([2, 1, 1])
        search = search_col.text_input("搜索", placeholder="项目、类别或备注")
        years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        year = year_col.selectbox("年份", ["全部"] + years)
        month = month_col.selectbox("月份", ["全部"] + list(range(1, 13)))
        type_col, category_col, sort_col = st.columns([1, 1.5, 1.5])
        tx_type = type_col.selectbox("类型", ["全部", EXPENSE, INCOME], format_func=lambda x: "全部" if x == "全部" else TYPE_LABELS[x])
        category = category_col.selectbox("类别", ["全部"] + categories)
        sort = sort_col.selectbox("排序", ["日期：最新优先", "日期：最早优先", "金额：由高到低", "金额：由低到高"])

    filtered = transactions.copy()
    if search:
        filtered = analytics.literal_search(filtered, search)
    if year != "全部": filtered = filtered[filtered["date"].dt.year == int(year)]
    if month != "全部": filtered = filtered[filtered["date"].dt.month == int(month)]
    if tx_type != "全部": filtered = filtered[filtered["type"] == tx_type]
    if category != "全部": filtered = filtered[filtered["category"] == category]
    sort_rules = {"日期：最新优先": ("date", False), "日期：最早优先": ("date", True), "金额：由高到低": ("amount", False), "金额：由低到高": ("amount", True)}
    col, asc = sort_rules[sort]
    filtered = filtered.sort_values([col, "id"], ascending=[asc, asc]).reset_index(drop=True)
    fi, fe, fb = analytics.calculate_totals(filtered)
    a, b, c, d = st.columns(4)
    a.metric("筛选结果", f"{len(filtered):,} 笔"); b.metric("支出", money(fe)); c.metric("收入", money(fi)); d.metric("净额", money(fb))

    view = st.segmented_control("显示方式", options=["表格", "卡片"], default="表格", key="transaction_view")
    if view == "卡片":
        _render_transaction_cards(filtered, categories)
        return

    st.caption("点击一行，可在下方查看、编辑或删除。")
    table = filtered.copy()
    table["日期"] = table["date"].dt.strftime("%Y-%m-%d")
    table["项目"] = table["item"]; table["类别"] = table["category"]; table["类型"] = table["type"].map(TYPE_LABELS)
    table["金额"] = table.apply(lambda r: r["amount"] if r["type"] == INCOME else -r["amount"], axis=1)
    table["备注"] = table["note"]; table["_id"] = table["id"]
    event = st.dataframe(table[["_id", "日期", "项目", "类别", "类型", "金额", "备注"]], column_order=["日期", "项目", "类别", "类型", "金额", "备注"], hide_index=True, use_container_width=True, height=560, on_select="rerun", selection_mode="single-row", key="transaction_table", column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})
    rows = event.selection.rows
    if rows and rows[0] < len(table):
        selected = table.iloc[rows[0]]
        original = filtered[filtered["id"] == selected["_id"]].iloc[0]
        st.markdown(safe_detail_html(original["item"], original["category"], TYPE_LABELS[original["type"]], original["amount"], str(original["date"].date()), original["note"], original["type"] == INCOME), unsafe_allow_html=True)
        edit_col, delete_col, _ = st.columns([1, 1, 4])
        if edit_col.button("编辑交易", type="primary", use_container_width=True): edit_transaction_dialog(int(original["id"]), categories)
        if delete_col.button("删除交易", use_container_width=True): delete_transaction_dialog(int(original["id"]))


def _reports_page(transactions: pd.DataFrame) -> None:
    page_header("分析报表", "年度、月度、类别与消费规律；金额全部由本地数据计算。")
    if transactions.empty:
        empty_state("暂无数据可分析"); return
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
    now = now_my()
    default_index = years.index(now.year) if now.year in years else 0
    year = int(st.selectbox("分析年份", years, index=default_index))
    annual = analytics.monthly_summary(transactions, year)
    year_all = transactions[transactions["date"].dt.year == year].copy()
    expenses = year_all[year_all["type"] == EXPENSE].copy()
    annual_expense = float(annual["支出"].sum()); annual_income = float(annual["收入"].sum())
    monthly_avg = analytics.average_monthly_expense(annual, year)
    savings = analytics.annual_savings_rate(annual)
    prior = analytics.monthly_summary(transactions, year - 1); prior_total = float(prior["支出"].sum())
    yoy = None if prior_total == 0 else (annual_expense - prior_total) / prior_total
    elapsed = analytics.elapsed_month_count(year)
    scope = annual[annual["month"] <= elapsed] if elapsed else annual
    highest = scope.loc[scope["支出"].idxmax()] if not scope.empty else annual.iloc[0]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("年度支出", money(annual_expense), "无去年数据" if yoy is None else f"{yoy:+.1%} 同比", delta_color="inverse")
    m2.metric("年度收入", money(annual_income)); m3.metric("截至目前月均", "N/A" if monthly_avg is None else money(monthly_avg)); m4.metric("储蓄率", "N/A" if savings is None else f"{savings:.1f}%"); m5.metric("最高月份", str(highest["月份"]), money(highest["支出"]), delta_color="off")

    quick, annual_tab, monthly_tab, category_tab, insight_tab = st.tabs(["快速总览", "年度趋势", "月度明细", "类别分析", "异常与规律"])
    with quick:
        left, right = st.columns([1.55, 1], gap="large")
        with left:
            section_title("全年每月支出")
            fig = px.bar(annual, x="月份", y="支出", text_auto=".0f")
            if monthly_avg is not None: fig.add_hline(y=monthly_avg, line_dash="dash", line_color="#F6BD16", annotation_text=f"月均 {money(monthly_avg)}")
            fig.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(fig, height=390)
        with right:
            section_title("年度类别占比")
            cat = expenses.groupby("category")["amount"].sum().sort_values(ascending=False).reset_index()
            if cat.empty: st.info("没有支出数据。")
            else:
                top = cat.head(7).copy()
                if len(cat) > 7: top = pd.concat([top, pd.DataFrame({"category": ["其他类别"], "amount": [cat.iloc[7:]["amount"].sum()]})], ignore_index=True)
                donut = px.pie(top, values="amount", names="category", hole=.56); donut.update_traces(textposition="inside", textinfo="percent", hovertemplate="%{label}<br>RM %{value:,.2f}<br>%{percent}<extra></extra>")
                render_chart(donut, height=390, legend=True, hovermode="closest")
        selector, _ = st.columns([1, 3]); qmonth = int(selector.selectbox("快速查看月份", range(1, 13), index=min(now.month - 1, 11), format_func=lambda v: f"{v}月", key="quick_month"))
        selected = analytics.month_slice(transactions, year, qmonth); selected_expenses = selected[selected["type"] == EXPENSE]
        days = calendar.monthrange(year, qmonth)[1]; daily = pd.DataFrame({"day": range(1, days + 1)})
        if not selected_expenses.empty:
            grouped = selected_expenses.assign(day=selected_expenses["date"].dt.day).groupby("day")["amount"].sum().reset_index(); daily = daily.merge(grouped, on="day", how="left")
        daily["amount"] = daily.get("amount", pd.Series(0.0, index=daily.index)).fillna(0.0)
        l, r = st.columns([1.45, 1], gap="large")
        with l:
            section_title("每日支出"); dfig = px.bar(daily, x="day", y="amount", labels={"day": "日期", "amount": "支出 (RM)"}); dfig.update_xaxes(dtick=1); dfig.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(dfig, height=355)
        with r:
            section_title("当月类别排行"); mcat = selected_expenses.groupby("category")["amount"].sum().sort_values().reset_index()
            if mcat.empty: st.info("该月没有支出。")
            else:
                h = px.bar(mcat, x="amount", y="category", orientation="h", labels={"amount": "支出 (RM)", "category": ""}); h.update_xaxes(rangemode="tozero", tickprefix="RM "); render_chart(h, height=355, hovermode="closest")
        with st.expander("查看月历"): st.markdown(_build_calendar_html(selected_expenses, year, qmonth), unsafe_allow_html=True)

    with annual_tab:
        section_title("收入、支出与结余")
        cash = annual.melt(id_vars=["month", "月份"], value_vars=["收入", "支出", "结余"], var_name="指标", value_name="金额")
        cfig = px.line(cash, x="月份", y="金额", color="指标", markers=True); cfig.update_yaxes(tickprefix="RM "); render_chart(cfig, height=390, legend=True)
        l, r = st.columns(2, gap="large")
        with l:
            section_title("累计支出：今年 vs 去年")
            compare = annual[["月份", "累计支出"]].rename(columns={"累计支出": str(year)}).copy(); compare[str(year - 1)] = prior["累计支出"].values
            melt = compare.melt(id_vars="月份", var_name="年份", value_name="累计支出"); f = px.line(melt, x="月份", y="累计支出", color="年份", markers=True); f.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=350, legend=True)
        with r:
            section_title("每月储蓄率")
            sf = px.bar(annual, x="月份", y="储蓄率", text_auto=".0f"); sf.add_hline(y=0, line_color="#EF6464"); sf.update_yaxes(ticksuffix="%"); render_chart(sf, height=350)
        section_title("类别随月份变化")
        if expenses.empty: st.info("没有支出数据。")
        else:
            stacked = expenses.assign(月份=expenses["date"].dt.month.map(lambda x: f"{x}月")).groupby(["月份", "category"])["amount"].sum().reset_index(); stacked["月份"] = pd.Categorical(stacked["月份"], categories=MONTH_LABELS, ordered=True); stacked = stacked.sort_values("月份")
            f = px.bar(stacked, x="月份", y="amount", color="category", labels={"amount": "支出 (RM)", "category": "类别"}); f.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=430, legend=True)

    with monthly_tab:
        selector, _ = st.columns([1, 3]); month = int(selector.selectbox("选择月份", range(1, 13), index=min(now.month - 1, 11), format_func=lambda v: f"{v}月", key="report_month"))
        selected = analytics.month_slice(transactions, year, month); mi, me, mb = analytics.calculate_totals(selected); expense_rows = selected[selected["type"] == EXPENSE].copy(); days = calendar.monthrange(year, month)[1]
        if year == now.year and month == now.month: elapsed_days = now.day; projected = me / max(elapsed_days, 1) * days
        elif year < now.year or (year == now.year and month < now.month): projected = me
        else: projected = None
        mm1, mm2, mm3, mm4, mm5 = st.columns(5); mm1.metric("收入", money(mi)); mm2.metric("支出", money(me)); mm3.metric("结余", money(mb)); mm4.metric("交易笔数", f"{len(selected):,}"); mm5.metric("月底预计", "N/A" if projected is None else money(projected), "按当前速度" if year == now.year and month == now.month else "实际", delta_color="off")
        if expense_rows.empty: st.info("该月没有支出。")
        else:
            expense_rows["day"] = expense_rows["date"].dt.day; daily_stacked = expense_rows.groupby(["day", "category"])["amount"].sum().reset_index(); section_title("每日支出及类别组成")
            f = px.bar(daily_stacked, x="day", y="amount", color="category", labels={"day": "日期", "amount": "支出 (RM)", "category": "类别"}); f.update_xaxes(dtick=1); f.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=420, legend=True)
            l, r = st.columns(2, gap="large")
            with l:
                section_title("平均每个星期几的支出")
                wd = analytics.weekday_average(expense_rows, year, month); f = px.bar(wd, x="星期", y="平均每个该星期", labels={"平均每个该星期": "平均支出 (RM)"}); f.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=350)
            with r:
                section_title("单笔金额分布")
                f = px.histogram(expense_rows, x="amount", nbins=min(16, max(5, len(expense_rows) // 3)), labels={"amount": "单笔金额 (RM)", "count": "笔数"}); f.update_xaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=350, hovermode="closest")
            with st.expander("查看月历"): st.markdown(_build_calendar_html(expense_rows, year, month), unsafe_allow_html=True)

    with category_tab:
        if expenses.empty: st.info("该年度没有支出。")
        else:
            data = expenses.groupby("category")["amount"].agg(["sum", "size", "mean"]).reset_index().rename(columns={"sum": "总支出", "size": "次数", "mean": "平均每笔"}).sort_values("总支出", ascending=False)
            l, r = st.columns([1.25, 1], gap="large")
            with l:
                section_title("类别金额排行"); hdata = data.sort_values("总支出"); f = px.bar(hdata, x="总支出", y="category", orientation="h", labels={"category": "", "总支出": "支出 (RM)"}); f.update_xaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=max(390, 34 * len(hdata)), hovermode="closest")
            with r:
                section_title("类别详细指标"); st.dataframe(data.rename(columns={"category": "类别"}), hide_index=True, use_container_width=True, height=430, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
            selected_category = st.selectbox("深入查看类别", data["category"].tolist()); rows = expenses[expenses["category"] == selected_category].copy(); monthly = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS}); grouped = rows.assign(month=rows["date"].dt.month).groupby("month")["amount"].sum().reset_index(name="支出"); monthly = monthly.merge(grouped, on="month", how="left").fillna(0)
            l, r = st.columns([1.4, 1], gap="large")
            with l:
                section_title(f"{selected_category} 的 12 个月趋势"); f = px.line(monthly, x="月份", y="支出", markers=True); f.update_yaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=340)
            with r:
                section_title("该类别项目排行"); rank = rows.groupby("item")["amount"].agg(["sum", "size"]).reset_index().rename(columns={"item": "项目", "sum": "总支出", "size": "次数"}).sort_values("总支出", ascending=False).head(12); st.dataframe(rank, hide_index=True, use_container_width=True, height=340, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f")})

    with insight_tab:
        section_title("快速结论")
        if expenses.empty: st.info("没有支出数据。")
        else:
            total = float(expenses["amount"].sum()); by_cat = expenses.groupby("category")["amount"].sum().sort_values(ascending=False)
            if not by_cat.empty and total > 0: st.markdown(f'<div class="wy-callout">最大类别是 {html.escape(str(by_cat.index[0]))}，占全年支出的 {by_cat.iloc[0] / total:.1%}。</div>', unsafe_allow_html=True)
        anomalies = analytics.anomaly_transactions(expenses); recurring = analytics.recurring_items(expenses); l, r = st.columns(2, gap="large")
        with l:
            section_title("异常高额交易"); st.caption("按各类别内部的四分位距寻找明显高于同类平常水平的交易。")
            if anomalies.empty: st.info("没有发现明显异常，或数据量不足。")
            else:
                show = anomalies[["date", "item", "category", "amount"]].head(20).copy(); show["date"] = show["date"].dt.strftime("%Y-%m-%d"); show.columns = ["日期", "项目", "类别", "金额"]; st.dataframe(show, hide_index=True, use_container_width=True, height=420, column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})
        with r:
            section_title("疑似固定／周期支出"); st.caption("综合月份覆盖、金额稳定性和时间间隔，不再把高频午餐/Grab简单当成订阅。")
            if recurring.empty: st.info("没有发现规律足够明显的周期支出。")
            else:
                show = recurring.head(20).copy(); show["最近日期"] = pd.to_datetime(show["最近日期"]).dt.strftime("%Y-%m-%d"); show["金额波动"] = show["金额波动"].map(lambda v: f"{v:.0%}"); st.dataframe(show, hide_index=True, use_container_width=True, height=420, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
        section_title("数据质量检查"); dq = analytics.data_quality(year_all); a, b, c = st.columns(3); a.metric("空项目名称", dq["blank_items"]); b.metric("零或负金额", dq["nonpositive_amounts"]); c.metric("疑似重复记录", dq["duplicates"])


def _ai_page(transactions: pd.DataFrame) -> None:
    page_header("AI 洞察", "Gemini 3.7 Flash 负责理解与解释；所有金额先由本地账本精确计算。")
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
    if not years:
        empty_state("暂无数据可分析"); return
    selected_year = int(st.selectbox("分析年份", years, key="ai_year"))
    data_signature = (len(transactions), int(transactions["id"].max()) if not transactions.empty else 0, round(float(transactions["amount"].sum()), 2) if not transactions.empty else 0.0, str(transactions["date"].max()) if not transactions.empty else "")
    if st.session_state.get("ai_data_signature") != data_signature or st.session_state.get("ai_scope_year") != selected_year:
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None)
        st.session_state["ai_data_signature"] = data_signature
        st.session_state["ai_scope_year"] = selected_year

    year_expenses = transactions[(transactions["date"].dt.year == selected_year) & (transactions["type"] == EXPENSE)].copy()
    classify_col, reset_col, _ = st.columns([1.2, 1, 3])
    if classify_col.button("AI 宏观归类", type="primary", use_container_width=True):
        try:
            with st.spinner("正在归类项目..."):
                mapping = categorize_macro(json.dumps(year_expenses["item"].dropna().unique().tolist(), ensure_ascii=False))
            result = year_expenses.copy(); result["宏观类别"] = result["item"].map(mapping).fillna("其他"); st.session_state["macro_result"] = result; st.session_state["macro_year"] = selected_year; st.rerun()
        except Exception as exc: st.error(f"AI 归类失败：{exc}")
    if reset_col.button("清除分析", use_container_width=True):
        st.session_state["ai_chat_history"] = []; st.session_state["ai_conversation_state"] = {}; st.session_state.pop("macro_result", None); st.rerun()
    if st.session_state.get("macro_year") == selected_year and isinstance(st.session_state.get("macro_result"), pd.DataFrame):
        macro = st.session_state["macro_result"].groupby("宏观类别")["amount"].sum().sort_values().reset_index(); f = px.bar(macro, x="amount", y="宏观类别", orientation="h", labels={"amount": "支出 (RM)", "宏观类别": ""}); f.update_xaxes(rangemode="tozero", tickprefix="RM "); render_chart(f, height=420, hovermode="closest")

    st.divider(); section_title("与账单对话")
    st.caption("AI 先把问题解析成结构化查询，再由本地 Pandas 从账本计算金额；不会把整本账本重复塞给模型。")
    history = st.session_state.setdefault("ai_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]): st.markdown(message["content"])
    question = st.chat_input("例如：8月打油多少钱？1到8月分别多少？那2025呢？")
    if question:
        history.append({"role": "user", "content": question})
        try:
            with st.chat_message("assistant"):
                with st.spinner("正在查账并分析..."):
                    plan = plan_finance_question(question, selected_year, transactions, st.session_state.get("ai_conversation_state"), history)
                    result = execute_finance_plan(plan, transactions)
                    reply = answer_finance_question(question, result)
                st.markdown(reply)
            history.append({"role": "assistant", "content": reply})
            st.session_state["ai_conversation_state"] = state_from_plan(plan, result)
        except Exception as exc:
            st.error(f"AI 对话失败：{exc}")


def _settings_page(transactions: pd.DataFrame, categories: list[str]) -> None:
    page_header("设置与备份", "管理类别，并下载包含交易、类别与元数据的完整备份。")
    category_tab, backup_tab = st.tabs(["类别管理", "备份"])
    with category_tab:
        registered = {value.casefold() for value in load_category_rows()}
        usage = transactions.groupby("category").agg(使用笔数=("amount", "size"), 累计金额=("amount", "sum")).reset_index().rename(columns={"category": "类别"}) if not transactions.empty else pd.DataFrame(columns=["类别", "使用笔数", "累计金额"])
        if not usage.empty: usage["状态"] = usage["类别"].map(lambda value: "已登记" if str(value).casefold() in registered else "历史记录未登记")
        st.dataframe(usage, hide_index=True, use_container_width=True, column_config={"累计金额": st.column_config.NumberColumn(format="RM %.2f")})
        missing = unregistered_categories(transactions)
        if missing:
            st.warning("发现历史交易中的未登记类别：" + "、".join(missing) + "。它们仍可筛选；可在下方建立或合并。")
        left, right = st.columns(2, gap="large")
        with left:
            section_title("新增类别"); name = st.text_input("类别名称", key="settings_new_category")
            if st.button("新增类别", type="primary", use_container_width=True):
                try:
                    created = create_category(name); st.toast("类别已建立" if created else "类别已存在"); st.rerun()
                except Exception as exc: st.error(f"新增失败：{exc}")
        with right:
            section_title("改名或合并类别")
            source = st.selectbox("原类别", categories, key="merge_source")
            mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True)
            choices = [value for value in categories if value.casefold() != source.casefold()]
            target = st.selectbox("目标类别", choices, key="merge_target") if mode == "现有类别" and choices else st.text_input("新类别名称", key="merge_new_name")
            source_count = int((transactions["category"] == source).sum()) if not transactions.empty else 0
            st.caption(f"将移动 {source_count} 笔交易。更新交易成功后才会删除原类别；中途失败不会删除交易。")
            confirm = st.checkbox("我确认执行类别合并", key="merge_confirm")
            if st.button("执行改名／合并", disabled=not confirm, use_container_width=True):
                try:
                    result = merge_category_safely(source, target); st.success(f"完成：移动 {result.moved_rows} 笔交易。"); st.rerun()
                except Exception as exc: st.error(f"合并失败：{exc}")

    with backup_tab:
        if transactions.empty:
            st.info("暂无交易可备份。")
        else:
            export = transactions.copy(); export["date"] = export["date"].dt.date
            registered_categories = pd.DataFrame({"name": load_category_rows()})
            metadata = pd.DataFrame([["export_time", now_my().isoformat()], ["timezone", TIMEZONE_NAME], ["currency", "MYR"], ["transaction_count", len(export)], ["registered_category_count", len(registered_categories)], ["app_version", "V2-stable"]], columns=["key", "value"])
            excel = io.BytesIO()
            with pd.ExcelWriter(excel, engine="xlsxwriter") as writer:
                export.to_excel(writer, index=False, sheet_name="Transactions"); registered_categories.to_excel(writer, index=False, sheet_name="Categories"); metadata.to_excel(writer, index=False, sheet_name="Metadata")
            d1, d2 = st.columns(2)
            d1.download_button("下载完整 Excel 备份", excel.getvalue(), f"WY_Wallet_V2_{today_my()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
            d2.download_button("下载交易 CSV", export.to_csv(index=False).encode("utf-8-sig"), f"WY_Wallet_V2_{today_my()}.csv", mime="text/csv", use_container_width=True)
            st.caption("Excel 包含 Transactions、Categories、Metadata 三个工作表。网页 V2 不再提供历史资料导入。")


def run() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
    inject_css()
    transactions = load_transactions()
    categories = _sorted_categories(transactions)
    if st.session_state.get("database_error"):
        st.error("数据库读取失败：" + st.session_state["database_error"])

    with st.sidebar:
        st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V2</div></div>', unsafe_allow_html=True)
        if st.button("＋ 新增交易", type="primary", use_container_width=True): add_transaction_dialog(categories)
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", use_container_width=True)
        navigation = st.radio("导航", ["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"], format_func=lambda v: {"总览": "⌂  总览", "交易记录": "≡  交易记录", "分析报表": "▥  分析报表", "AI 洞察": "✦  AI 洞察", "设置与备份": "⚙  设置与备份"}[v], label_visibility="collapsed")
        st.divider()
        if st.button("↻ 刷新数据", use_container_width=True): refresh_data(); st.rerun()
        st.caption(f"Malaysia time · {TIMEZONE_NAME}")
        st.caption("V2 与旧网页共用现有 Supabase 数据")

    if navigation == "总览": _dashboard(transactions)
    elif navigation == "交易记录": _transactions_page(transactions, categories)
    elif navigation == "分析报表": _reports_page(transactions)
    elif navigation == "AI 洞察": _ai_page(transactions)
    else: _settings_page(transactions, categories)
