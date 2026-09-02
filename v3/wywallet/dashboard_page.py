from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from . import analytics
from .access import touch_access
from .config import EXPENSE, REFUND, now_my
from .ui import empty_state, money, page_header, render_chart, section_title


def _recent_months_summary(frame: pd.DataFrame, periods: int = 12) -> pd.DataFrame:
    """Build a calendar-anchored month window ending at the Malaysia current month.

    Using explicit year/month keys avoids stale categorical/Period alignment and
    guarantees the current month is always represented as the final bar, even
    when it only has partial-month data.
    """
    now = now_my()
    current_period = pd.Period(year=now.year, month=now.month, freq="M")
    month_periods = pd.period_range(end=current_period, periods=int(periods), freq="M")
    base = pd.DataFrame(
        {
            "period": month_periods,
            "year": [value.year for value in month_periods],
            "month": [value.month for value in month_periods],
            "月份": [value.strftime("%Y-%m") for value in month_periods],
        }
    )

    effects = analytics.expense_effect_frame(frame)
    if effects.empty:
        base["支出"] = 0.0
    else:
        work = effects.copy()
        dates = pd.to_datetime(work["date"], errors="coerce")
        work = work.loc[dates.notna()].copy()
        dates = pd.to_datetime(work["date"], errors="coerce")
        work["year"] = dates.dt.year.astype(int)
        work["month"] = dates.dt.month.astype(int)
        grouped = work.groupby(["year", "month"], as_index=False)["expense_effect"].sum()
        grouped = grouped.rename(columns={"expense_effect": "支出"})
        base = base.merge(grouped, on=["year", "month"], how="left")
        base["支出"] = pd.to_numeric(base["支出"], errors="coerce").fillna(0.0)

    base["是否当月未完整"] = (base["year"] == now.year) & (base["month"] == now.month)
    base["显示月份"] = base["月份"] + base["是否当月未完整"].map({True: "*", False: ""})
    return base.sort_values(["year", "month"]).reset_index(drop=True)


def render(transactions: pd.DataFrame) -> None:
    touch_access()
    page_header("财务总览", "净支出自动扣除退款；月度变化使用上月同期，月底预测使用近期历史剩余日期模式。")

    now = now_my()
    current = analytics.month_slice(transactions, now.year, now.month)
    previous_same_period = analytics.previous_month_same_elapsed_slice(
        transactions, now.year, now.month, now.day
    )
    income, expense, balance = analytics.calculate_totals(current)
    _, prior_expense, _ = analytics.calculate_totals(previous_same_period)
    flows = analytics.calculate_flow_totals(current)
    change = None if prior_expense == 0 else (expense - prior_expense) / abs(prior_expense)
    forecast = analytics.historical_month_end_forecast(transactions, now.year, now.month, now.day)

    # One equal-width row keeps all primary numbers visually comparable.
    m1, m2, m3, m4, m5 = st.columns(5, gap="small")
    m1.metric("本月收入", money(income))
    m2.metric(
        "本月净支出",
        money(expense),
        "无上月同期数据" if change is None else f"{change:+.1%} 对比上月同期",
        delta_color="inverse",
    )
    m3.metric("本月结余", money(balance))
    m4.metric("本月退款", money(flows["refund"]))
    m5.metric(
        "月底预计净支出",
        money(float(forecast["forecast"])),
        f"历史中位数 · {forecast['history_months']}个月样本"
        if forecast["history_months"]
        else "历史样本不足，以当前实际为准",
        delta_color="off",
    )
    if forecast.get("history_months"):
        st.caption(
            f"月底预测历史区间：{money(float(forecast['low']))} ～ {money(float(forecast['high']))}"
        )

    left, right = st.columns([1.65, 1], gap="large")
    with left:
        section_title("最近 12 个月净支出")
        twelve = _recent_months_summary(transactions)
        fig = px.bar(
            twelve,
            x="显示月份",
            y="支出",
            text_auto=".0f",
            category_orders={"显示月份": twelve["显示月份"].tolist()},
        )
        fig.update_xaxes(type="category")
        fig.update_yaxes(tickprefix="RM ")
        render_chart(fig, height=335)
        st.caption("* 当前月份尚未结束，柱值为截至今天实际净支出。")

    with right:
        section_title("本月类别净支出")
        summary = analytics.net_expense_by_category(current)
        positive = summary[summary["amount"] > 0].head(7)
        if positive.empty:
            empty_state("本月暂无净支出")
        else:
            denominator = float(summary.loc[summary["amount"] > 0, "amount"].sum()) or 1.0
            for _, row in positive.iterrows():
                label, value = st.columns([1.6, 1])
                label.write(f"**{row['category']}**")
                value.write(money(row["amount"]))
                st.progress(min(max(float(row["amount"] / denominator), 0.0), 1.0))
        negative = summary[summary["amount"] < 0]
        if not negative.empty:
            st.caption(
                "净退款类别："
                + "；".join(
                    f"{row['category']} {money(-row['amount'])}"
                    for _, row in negative.head(5).iterrows()
                )
            )

    section_title("最近交易")
    if transactions.empty:
        empty_state("还没有任何交易记录")
        return

    recent = transactions.copy()
    sort_columns = [column for column in ["date", "id"] if column in recent.columns]
    if sort_columns:
        recent = recent.sort_values(sort_columns, ascending=[False] * len(sort_columns))
    recent = recent.head(8).copy()
    type_labels = {EXPENSE: "支出", "Income": "收入", REFUND: "退款"}
    st.table(
        pd.DataFrame(
            {
                "日期": recent["date"].dt.strftime("%Y-%m-%d"),
                "项目": recent["item"],
                "类别": recent["category"],
                "类型": recent["type"].map(type_labels),
                "金额": recent.apply(
                    lambda row: ("+" if row["type"] in {"Income", REFUND} else "−") + money(row["amount"]),
                    axis=1,
                ),
            }
        )
    )
