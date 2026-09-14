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
    try_local_finance_answer,
)
from .config import EXPENSE, REFUND, TYPE_LABELS
from .db import ledger_signature
from .snapshot import fresh_snapshot
from .ui import page_header, render_chart, section_title
from .ux import page_slice


def _friendly_ai_query_error(exc: Exception) -> str:
    text = str(exc).casefold()
    if "429" in text or "resource_exhausted" in text or "resource exhausted" in text or "quota" in text:
        if "perday" in text or "per_day" in text or "requestsperday" in text or "free_tier_requests" in text:
            return (
                "Gemini 免费层今天的请求额度已用完。WY Wallet 的本地精确查询仍然可用；"
                "常见金额、月份、比较，以及加油/餐饮分开统计会优先直接由 Python 处理，不再浪费 Gemini 请求。"
            )
        return "Gemini 当前请求频率受限，请稍后再试；本地可识别的财务查询仍会继续工作。"
    if "503" in text or "unavailable" in text or "high demand" in text:
        return "Gemini 当前繁忙；请稍后再试。可以本地计算的账单问题不受影响。"
    if "timeout" in text or "timed out" in text or "deadline" in text:
        return "Gemini 本次响应超时；请重试。可以本地计算的账单问题不受影响。"
    detail = str(exc).strip().replace("\n", " ")
    if len(detail) > 220:
        detail = detail[:217] + "..."
    return f"AI 查询失败：{detail}" if detail else "AI 查询失败，请重试。"


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
    _, start, end = page_slice("分页", "ai_release_page", len(frame), 100)
    show = frame.iloc[start:end][["date", "item", "category", "type", "amount", "note"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show["type"] = show["type"].map(TYPE_LABELS)
    st.dataframe(show, hide_index=True, width="stretch", height=520, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})
    st.caption(f"共 {len(frame):,} 笔；当前显示第 {start + 1:,}–{end:,} 笔。")


def render(transactions: pd.DataFrame) -> None:
    touch_access()
    page_header("AI 洞察", "Gemini 3.7 只负责理解真正模糊的问题；数字、退款、日期、平均、比较和常见账单查询优先由 Python 本地计算。")
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
            st.error(_friendly_ai_query_error(exc).replace("AI 查询失败", "AI 归类失败", 1))
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
    st.caption("金额、列表、比较和常见月度查询由 Python 精确计算；只有真正需要语义理解时才调用 Gemini。每次提问只 fresh 读取一次数据库 snapshot。")
    history = st.session_state.setdefault("ai_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("例如：今年每个月加油和餐饮分开看；本月为什么比上月高？最大一笔支出？")
    if question:
        try:
            with st.chat_message("user"):
                st.markdown(question)
            with st.chat_message("assistant"):
                with st.spinner("正在读取最新账本并计算..."):
                    fresh = fresh_snapshot()["transactions"]
                    current_state = st.session_state.get("ai_conversation_state") or {}
                    direct = try_local_finance_answer(question, selected_year, fresh, current_state)
                    plan = None
                    result = None
                    explanation = ""
                    if direct is not None:
                        summary = str(direct["markdown"])
                        next_state = dict(direct["state"])
                    else:
                        plan = plan_finance_question(question, selected_year, fresh, current_state, history)
                        result = execute_finance_plan(plan, fresh)
                        summary = authoritative_summary_markdown(result)
                        explanation = answer_finance_question(question, result)
                        next_state = state_from_plan(plan, result)
                st.markdown(summary)
                if explanation:
                    st.caption("差异解释")
                    st.markdown(explanation)
            history.extend([
                {"role": "user", "content": question},
                {"role": "assistant", "content": summary + ("\n\n" + explanation if explanation else "")},
            ])
            st.session_state["ai_chat_history"] = history[-30:]
            st.session_state["ai_conversation_state"] = next_state
            st.session_state["ai_data_signature"] = ledger_signature(fresh)
            if plan is not None and plan.intent == "list":
                st.session_state["ai_last_list_plan"] = plan.model_dump()
                st.rerun()
            else:
                st.session_state.pop("ai_last_list_plan", None)
        except Exception as exc:
            st.error(_friendly_ai_query_error(exc))

    if st.session_state.get("ai_last_list_plan"):
        _render_list(st.session_state["ai_last_list_plan"], transactions)
