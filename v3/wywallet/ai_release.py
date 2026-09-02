from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

from . import ai as base
from .config import today_my


def _local_subject_matches(subject: str, frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    subject_text = base._normalize_text(subject)
    aliases: list[str] = []
    for triggers, candidates in base.SEMANTIC_GROUPS:
        if any(base._alias_match(subject_text, trigger) for trigger in triggers):
            aliases.extend(candidates)

    def matches(value: str) -> bool:
        normalized = base._normalize_text(value)
        if normalized == subject_text:
            return True
        if base._alias_match(normalized, subject_text):
            return True
        return any(base._alias_match(normalized, alias) for alias in aliases)

    items = [value for value in base._candidate_values(frame, "item", 100_000) if matches(value)]
    categories = [value for value in base._candidate_values(frame, "category", 100_000) if matches(value)]
    return items, categories


def plan_finance_question(question: str, selected_year: int, transactions: pd.DataFrame,
                          conversation_state: dict | None, recent_history: list[dict] | None) -> base.FinanceQueryPlan:
    plan = base.plan_finance_question(question, selected_year, transactions, conversation_state, recent_history)
    if plan.subject_mode == "specific" and plan.subject:
        items, categories = _local_subject_matches(plan.subject, transactions)
        plan.matched_items = items
        plan.matched_categories = categories
    return plan


def _completed_month_average(plan: base.FinanceQueryPlan, transactions: pd.DataFrame,
                             start: date, end: date, flow: str) -> float:
    today = today_my()
    current_month_start = date(today.year, today.month, 1)
    completed_end = current_month_start - timedelta(days=1)

    base_frame, _, _ = base._filter_subject(transactions.copy(), plan)
    effective_end = min(end, today)
    if end >= current_month_start and start <= completed_end:
        effective_end = min(effective_end, completed_end)
    ranged = base._filter_range(base_frame, start, effective_end) if start <= effective_end else base_frame.iloc[0:0].copy()
    total = base._amount_total(ranged, flow)
    months = (effective_end.year - start.year) * 12 + effective_end.month - start.month + 1
    return round(total / max(months, 1), 2)


def execute_finance_plan(plan: base.FinanceQueryPlan, transactions: pd.DataFrame) -> dict:
    result = base.execute_finance_plan(plan, transactions)
    aggregation = plan.aggregation or "amount"
    flow = plan.flow or "expense"

    if aggregation == "average_month":
        start = base._parse_iso(plan.date_from) or date(today_my().year, 1, 1)
        end = min(base._parse_iso(plan.date_to) or date(today_my().year, 12, 31), today_my())
        result["authoritative_total"] = _completed_month_average(plan, transactions, start, end, flow)

        comp_range = base._comparison_range(plan, start, end)
        if result.get("comparison") and comp_range:
            comp_start, comp_end = comp_range
            comp_end = min(comp_end, today_my())
            value = _completed_month_average(plan, transactions, comp_start, comp_end, flow)
            delta = round(result["authoritative_total"] - value, 2)
            result["comparison"]["value"] = value
            result["comparison"]["delta"] = delta
            result["comparison"]["percent"] = None if value <= 0 else delta / value * 100

    comparison = result.get("comparison")
    if comparison is not None:
        value = float(comparison.get("value") or 0)
        if value <= 0:
            comparison["percent"] = None

    return result


FinanceQueryPlan = base.FinanceQueryPlan
answer_finance_question = base.answer_finance_question
authoritative_summary_markdown = base.authoritative_summary_markdown
categorize_macro = base.categorize_macro
finance_list_frame = base.finance_list_frame
state_from_plan = base.state_from_plan
