from __future__ import annotations

import json

import pandas as pd
import plotly.express as px
import streamlit as st

from .access import touch_access
from .ai_release import (
    FinanceQueryPlan,
    answer_finance_question,
    authoritative_summary_markdown,
    categorize_macro,
    execute_finance_plan,
    finance_list_frame,
    plan_finance_question,
    state_from_plan,
)
from .config import EXPENSE, REFUND, TYPE_LABELS
from .db import ledger_signature
from .snapshot import fresh_snapshot
from .ui import page_header, render_chart, section_title


def _render_list(plan_dict: dict, transactions: pd.DataFrame) -> None:
    try:
        plan = FinanceQueryPlan.model_validate(plan_dict)
        frame = finance_list_frame(plan, transactions)
    except Exception:
        st.session_state.pop("ai_last_list_plan", None)
        return
    section_title("完整本地查询结果")
    if frame.empty:
        st.info("没有匹配记录。")
        return
    page_size = 100
    page_count = max(1, (len(frame) + page_size - 1) // page_size)
    page = int(st.selectbox("分页", range(1, page_count + 1), format_func=lambda value: f"第 {value}/{page_count} 页", key="ai_release_page")) if page_count > 1 else 1
    start = (page - 1) * page_size
    end = min(start + page_size, len(frame))
    show = frame.iloc[start:end][["date", "item", "category", "type", "amount", "note"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show["type"] = show["type"].map(TYPE_LABELS)
    st.dataframe(show, hide_index=True, width="stretch", height=520, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})
    st.caption(f"共 {len(frame):,} 笔；当前显示第 {start + 1:,}–{end:,} 笔。")


def render(transactions: pd.DataFrame) -> None:
    touch_access()
    page_header("AI 洞察", "Gemini 3.7 只负责理解和解释；数字、退款、日期、平均和比较全部由 Python 本地计算。")
    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
    if not years:
        st.info("暂无数据可分析。")
        return

    selected_year = int(st.selectbox("分析年份", years, key="ai_year"))
    signature = ledger_signature(transactions)
    if st.session_state.get("ai_data_signature") != signature or st.session_state.get("ai_scope_year") != selected_year:
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None)
        st.session_state.pop("ai_last_list_plan", None)
        st.session_state["ai_data_signature"] = signature
        st.session_state["ai_scope_year"] = selected_year

    year_effects = transactions[
        (transactions["date"].dt.year == selected_year)
        & (transactions["type"].isin([EXPENSE, REFUND]))
    ].copy()
    classify, reset, _ = st.columns([1.2, 1, 3])
    if classify.button("AI 宏观归类", type="primary", width="stretch"):
        try:
            with st.spinner("正在分批归类项目..."):
                mapping = categorize_macro(json.dumps(year_effects["item"].dropna().unique().tolist(), ensure_ascii=False))
            result = year_effects.copy()
            result["宏观类别"] = result["item"].map(mapping).fillna("其他")
            result["净支出影响"] = result["amount"].where(result["type"] == EXPENSE, -result["amount"])
            st.session_state["macro_result"] = result
            st.session_state["macro_year"] = selected_year
            st.rerun()
        except Exception as exc:
            st.error(f"AI 归类失败：{exc}")
    if reset.button("清除分析", width="stretch"):
        st.session_state["ai_chat_history"] = []
        st.session_state["ai_conversation_state"] = {}
        st.session_state.pop("macro_result", None)
        st.session_state.pop("ai_last_list_plan", None)
        st.rerun()

    if st.session_state.get("macro_year") == selected_year and isinstance(st.session_state.get("macro_result"), pd.DataFrame):
        macro = st.session_state["macro_result"].groupby("宏观类别")["净支出影响"].sum().sort_values().reset_index()
        fig = px.bar(macro, x="净支出影响", y="宏观类别", orientation="h", labels={"净支出影响": "净支出 (RM)", "宏观类别": ""})
        fig.update_xaxes(tickprefix="RM ")
        render_chart(fig, height=420)

    st.divider()
    section_title("与账单对话")
    st.caption("金额、列表和比较由 Python 精确计算；查询时只 fresh 读取一次数据库 snapshot。")
    history = st.session_state.setdefault("ai_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("例如：1到8月平均每月打油多少？8月跟6月比？最大一笔支出？")
    if question:
        try:
            with st.chat_message("assistant"):
                with st.spinner("正在读取最新账本并计算..."):
                    fresh = fresh_snapshot()["transactions"]
                    plan = plan_finance_question(question, selected_year, fresh, st.session_state.get("ai_conversation_state"), history)
                    result = execute_finance_plan(plan, fresh)
                    summary = authoritative_summary_markdown(result)
                    explanation = answer_finance_question(question, result)
                st.markdown(summary)
                if explanation:
                    st.caption("AI 解释")
                    st.markdown(explanation)
            history.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": summary + ("\n\n" + explanation if explanation else "")},
            ])
            st.session_state["ai_chat_history"] = history[-30:]
            st.session_state["ai_conversation_state"] = state_from_plan(plan, result)
            st.session_state["ai_data_signature"] = ledger_signature(fresh)
            if plan.intent == "list":
                st.session_state["ai_last_list_plan"] = plan.model_dump()
                st.rerun()
            else:
                st.session_state.pop("ai_last_list_plan", None)
        except Exception as exc:
            st.error(f"AI 查询失败：{exc}")

    if st.session_state.get("ai_last_list_plan"):
        _render_list(st.session_state["ai_last_list_plan"], transactions)
