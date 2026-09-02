from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from . import analytics
from .access import touch_access
from .config import EXPENSE, REFUND, now_my
from .product_logic import first_complete_tracking_month, historical_monthly_average, invalid_quality_for_year, recurring_items_by_category, tracking_start_date
from .ui import empty_state, money, page_header, render_chart, section_title


def _pie_with_other(category_summary: pd.DataFrame, top_n: int = 8) -> pd.DataFrame:
    positive = category_summary[category_summary["amount"] > 0].copy().sort_values("amount", ascending=False)
    if len(positive) <= top_n:
        return positive
    top = positive.head(top_n).copy()
    remainder = float(positive.iloc[top_n:]["amount"].sum())
    if remainder > 0:
        top = pd.concat([top, pd.DataFrame([{"category": "其余类别", "amount": remainder}])], ignore_index=True)
    return top


def render(transactions: pd.DataFrame, invalid_rows: pd.DataFrame) -> None:
    touch_access()
    page_header("分析报表", "退款会抵减对应类别支出；当前年度只显示截至今天已发生的数据。")
    if transactions.empty:
        empty_state("暂无有效数据可分析")
        return

    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
    now = now_my()
    year = int(st.selectbox("分析年份", years, index=years.index(now.year) if now.year in years else 0, key="report_year"))
    annual = analytics.monthly_summary(transactions, year)
    year_all = transactions[transactions["date"].dt.year == year].copy()
    elapsed = analytics.elapsed_month_count(year)
    current_year = year == now.year
    display_annual = annual[annual["month"] <= elapsed].copy() if current_year else annual.copy()

    annual_expense = float(display_annual["支出"].sum())
    annual_income = float(display_annual["收入"].sum())
    annual_refund = float(display_annual["退款"].sum())
    monthly_avg = historical_monthly_average(annual, year, transactions)
    savings = analytics.annual_savings_rate(annual)
    yoy = analytics.same_period_yoy(transactions, year)
    highest = display_annual.loc[display_annual["支出"].idxmax()] if not display_annual.empty else None
    first = tracking_start_date(transactions)
    first_complete = first_complete_tracking_month(transactions, year)
    if current_year:
        if first_complete is not None and first_complete <= now.month - 1:
            avg_label = "完整追踪月份月均"
        else:
            avg_label = f"{now.month}月截至目前"
    else:
        partial_history_year = first is not None and first.year == year and (first.month > 1 or first.day > 1)
        avg_label = "完整追踪月份月均" if partial_history_year else "全年月均"
    prefix = "截至目前" if current_year else "年度"
    yoy_text = "无同期数据" if not yoy or yoy["change"] is None else f"{yoy['change']:+.1%} 同期同比"

    m1, m2, m3, m4, m5 = st.columns(5, gap="small")
    m1.metric(f"{prefix}净支出", money(annual_expense), yoy_text, delta_color="inverse")
    m2.metric(f"{prefix}收入", money(annual_income))
    m3.metric(f"{prefix}退款", money(annual_refund))
    m4.metric(avg_label, "N/A" if monthly_avg is None else money(monthly_avg))
    m5.metric("储蓄率", "N/A" if savings is None else f"{savings:.1f}%")
    if highest is not None and float(highest["支出"]) > 0:
        st.caption(f"最高净支出月份：{highest['月份']} · {money(highest['支出'])}")
    elif highest is not None:
        st.caption("本年度没有正净支出月份。")
    if yoy:
        st.caption(f"同比口径：{yoy['current_start']}–{yoy['current_end']} 对比 {yoy['previous_start']}–{yoy['previous_end']}。")
    if first is not None and first.year == year:
        if first.day > 1:
            if first_complete is not None:
                st.caption(
                    f"{year} 年账本从 {first:%Y-%m-%d} 开始；{first.month}月是不完整追踪月，"
                    f"因此月均从 {first_complete} 月起按完整月份计算，追踪开始前的月份也不当作 0 元。"
                )
            else:
                st.caption(f"{year} 年账本从 {first:%Y-%m-%d} 开始且没有完整追踪月份，因此不把该不完整月份伪装成整月平均。")
        elif first.month > 1:
            st.caption(f"{year} 年账本从 {first:%Y-%m-%d} 开始，因此月均只按实际完整追踪月份计算，不把追踪开始前的月份当作 0 元月份。")

    section = st.segmented_control(
        "报表区块",
        ["快速总览", "年度趋势", "月度明细", "类别分析", "异常与规律"],
        default="快速总览",
        key="report_section",
    )

    if section == "快速总览":
        left, right = st.columns([1.55, 1], gap="large")
        with left:
            section_title("每月净支出")
            chart_annual = display_annual.copy()
            chart_annual["显示月份"] = chart_annual["月份"]
            if current_year and not chart_annual.empty:
                mask = chart_annual["month"] == now.month
                chart_annual.loc[mask, "显示月份"] = chart_annual.loc[mask, "月份"] + "*"
            fig = px.bar(chart_annual, x="显示月份", y="支出", text_auto=".0f")
            if monthly_avg is not None:
                fig.add_hline(y=monthly_avg, line_dash="dash", annotation_text=f"月均 {money(monthly_avg)}")
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=390)
            if current_year:
                st.caption("* 当前月份尚未结束，数值为截至今天实际净支出。")
        with right:
            section_title("年度类别净支出")
            category_summary = analytics.net_expense_by_category(year_all)
            pie = _pie_with_other(category_summary)
            negative = category_summary[category_summary["amount"] < 0]
            if pie.empty:
                st.info("没有正的类别净支出。")
            else:
                render_chart(px.pie(pie, values="amount", names="category", hole=.56), height=390, legend=True, hovermode="closest")
                if len(category_summary[category_summary["amount"] > 0]) > 8:
                    st.caption("饼图显示前 8 个类别，并将其余正净支出合并为「其余类别」，百分比仍代表全年完整正净支出。")
            if not negative.empty:
                st.caption("本年度净退款类别：" + "；".join(f"{row['category']} {money(-row['amount'])}" for _, row in negative.head(6).iterrows()))

    elif section == "年度趋势":
        section_title("收入、净支出与结余")
        cash = display_annual.melt(id_vars=["month", "月份"], value_vars=["收入", "支出", "结余"], var_name="指标", value_name="金额")
        fig = px.line(cash, x="月份", y="金额", color="指标", markers=True)
        fig.update_yaxes(tickprefix="RM ")
        render_chart(fig, height=390, legend=True)

        section_title("累计净支出同比")
        prior_rows = transactions[transactions["date"].dt.year == year - 1]
        if prior_rows.empty:
            st.info(f"{year - 1} 年没有账本数据，因此不绘制虚假的 0 元同比曲线。")
        else:
            prior = analytics.monthly_summary(transactions, year - 1)
            prior = prior[prior["month"] <= elapsed].copy() if current_year else prior
            compare = display_annual[["月份", "累计支出"]].rename(columns={"累计支出": str(year)}).copy()
            compare[str(year - 1)] = prior["累计支出"].values[: len(compare)]
            melt = compare.melt(id_vars="月份", var_name="年份", value_name="累计净支出")
            fig = px.line(melt, x="月份", y="累计净支出", color="年份", markers=True)
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=350, legend=True)

    elif section == "月度明细":
        month_options = list(range(1, now.month + 1)) if current_year else list(range(1, 13))
        default_month_index = len(month_options) - 1 if current_year else min(now.month - 1, 11)
        month = int(st.selectbox("选择月份", month_options, index=default_month_index, format_func=lambda value: f"{value}月", key="report_month"))
        selected = analytics.month_slice(transactions, year, month)
        income, expense, balance = analytics.calculate_totals(selected)
        flows = analytics.calculate_flow_totals(selected)
        is_current_month = current_year and month == now.month
        forecast = analytics.historical_month_end_forecast(transactions, year, month, now.day) if is_current_month else None

        a, b, c, d, e = st.columns(5, gap="small")
        a.metric("收入", money(income))
        b.metric("净支出", money(expense))
        c.metric("退款", money(flows["refund"]))
        d.metric("结余", money(balance))
        e.metric(
            "月底历史预测" if is_current_month else "实际净支出",
            money(float(forecast["forecast"]) if forecast else expense),
            f"{forecast['history_months']}个月样本" if forecast and forecast["history_months"] else "实际",
            delta_color="off",
        )
        if forecast and forecast.get("history_months"):
            st.caption(f"历史预测区间：{money(float(forecast['low']))} ～ {money(float(forecast['high']))}")

        effects = analytics.expense_effect_frame(selected)
        if effects.empty:
            st.info("该月没有支出或退款。")
        else:
            daily = effects.assign(day=effects["date"].dt.day).groupby("day")["expense_effect"].sum().reset_index(name="amount")
            fig = px.bar(daily, x="day", y="amount", labels={"day": "日期", "amount": "净支出 (RM)"})
            fig.update_xaxes(dtick=1)
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=370)
            weekday = analytics.weekday_average(selected, year, month)
            fig = px.bar(weekday, x="星期", y="平均每个该星期", labels={"平均每个该星期": "平均净支出 (RM)"})
            fig.update_yaxes(tickprefix="RM ")
            render_chart(fig, height=330)

    elif section == "类别分析":
        effects = analytics.expense_effect_frame(year_all)
        if effects.empty:
            st.info("该年度没有支出或退款。")
        else:
            effects["毛支出"] = effects["amount"].where(effects["type"] == EXPENSE, 0.0)
            effects["退款"] = effects["amount"].where(effects["type"] == REFUND, 0.0)
            data = (
                effects.groupby("category")
                .agg(毛支出=("毛支出", "sum"), 退款=("退款", "sum"), 净支出=("expense_effect", "sum"), 交易笔数=("amount", "size"))
                .reset_index()
                .sort_values("净支出", ascending=False)
            )
            chart = data.assign(_impact=data["净支出"].abs()).nlargest(15, "_impact").sort_values("净支出")
            fig = px.bar(chart, x="净支出", y="category", orientation="h", labels={"category": "", "净支出": "净支出 (RM)"})
            fig.update_xaxes(tickprefix="RM ")
            render_chart(fig, height=max(390, 34 * len(chart)))
            if len(data) > len(chart):
                st.caption(f"图表只显示净影响绝对值最大的 {len(chart)} 个类别；下表保留全部 {len(data)} 个类别。")
            st.dataframe(
                data.rename(columns={"category": "类别"}),
                hide_index=True,
                width="stretch",
                column_config={
                    "毛支出": st.column_config.NumberColumn(format="RM %.2f"),
                    "退款": st.column_config.NumberColumn(format="RM %.2f"),
                    "净支出": st.column_config.NumberColumn(format="RM %.2f"),
                },
            )

    else:
        anomalies = analytics.anomaly_transactions(year_all)
        recurring = recurring_items_by_category(year_all)
        left, right = st.columns(2, gap="large")
        with left:
            section_title("异常高额支出")
            if anomalies.empty:
                st.info("没有发现明显异常，或数据量不足。")
            else:
                show = anomalies[["date", "item", "category", "amount"]].head(20).copy()
                show["date"] = show["date"].dt.strftime("%Y-%m-%d")
                st.dataframe(show, hide_index=True, width="stretch", column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})
        with right:
            section_title("疑似固定／周期支出")
            if recurring.empty:
                st.info("没有发现规律足够明显的周期支出。")
            else:
                show = recurring.head(20).copy()
                show["最近日期"] = pd.to_datetime(show["最近日期"]).dt.strftime("%Y-%m-%d")
                show["金额波动"] = show["金额波动"].map(lambda value: f"{value:.0%}")
                st.dataframe(show, hide_index=True, width="stretch")
        quality = analytics.data_quality(year_all)
        invalid_for_year, invalid_unassigned = invalid_quality_for_year(invalid_rows, year)
        a, b, c, d = st.columns(4)
        a.metric("有效交易", f"{len(year_all):,}")
        b.metric("疑似重复记录", quality["duplicates"])
        c.metric("本年无效记录", invalid_for_year)
        d.metric("无效日期无法归年", invalid_unassigned)
