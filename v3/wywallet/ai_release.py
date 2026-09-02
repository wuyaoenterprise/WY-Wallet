from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

from . import ai as base
from .config import today_my
from .product_logic import tracking_start_date


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

    first = tracking_start_date(transactions)
    effective_start = max(start, first) if first is not None else start
    if effective_start > effective_end:
        return 0.0

    # If the requested/tracked range starts in the middle of a month and later
    # complete months exist, exclude that first partial calendar month from both
    # numerator and denominator. If it is the only available month, retain the
    # actual-to-date value instead of manufacturing a zero/empty average.
    if effective_start.day > 1:
        next_month = (pd.Timestamp(effective_start).replace(day=1) + pd.DateOffset(months=1)).date()
        if next_month <= effective_end:
            effective_start = next_month

    ranged = base._filter_range(base_frame, effective_start, effective_end)
    total = base._amount_total(ranged, flow)
    months = (effective_end.year - effective_start.year) * 12 + effective_end.month - effective_start.month + 1
    return round(total / max(months, 1), 2)


def _tracked_day_average(plan: base.FinanceQueryPlan, transactions: pd.DataFrame,
                         start: date, end: date, flow: str) -> tuple[float, date | None, date | None]:
    """Average over days for which the ledger was actually under tracking."""
    first = tracking_start_date(transactions)
    effective_start = max(start, first) if first is not None else start
    effective_end = min(end, today_my())
    if effective_start > effective_end:
        return 0.0, None, None
    base_frame, _, _ = base._filter_subject(transactions.copy(), plan)
    ranged = base._filter_range(base_frame, effective_start, effective_end)
    total = base._amount_total(ranged, flow)
    days = (effective_end - effective_start).days + 1
    return round(total / max(days, 1), 2), effective_start, effective_end


def _sanitize_monthly_coverage(result: dict, transactions: pd.DataFrame) -> None:
    """Remove pre-tracking fake zero months and mark partial observations."""
    rows = list(result.get("monthly") or [])
    if not rows:
        return
    first = tracking_start_date(transactions)
    today = today_my()
    cleaned: list[dict] = []
    for original in rows:
        row = dict(original)
        try:
            period = pd.Period(str(row.get("period") or row.get("label")), freq="M")
        except Exception:
            cleaned.append(row)
            continue
        period_start = period.start_time.date()
        period_end = period.end_time.date()
        if first is not None and period_end < first:
            continue
        partial_tracking = bool(
            first is not None
            and first.year == period.year
            and first.month == period.month
            and first.day > 1
        )
        partial_current = bool(
            period.year == today.year
            and period.month == today.month
            and today.day < calendar.monthrange(today.year, today.month)[1]
        )
        row["partial_tracking"] = partial_tracking
        row["partial_current"] = partial_current
        label = str(row.get("label") or period.strftime("%Y-%m"))
        if partial_tracking:
            label += "†"
        if partial_current:
            label += "*"
        row["label"] = label
        cleaned.append(row)

    result["monthly"] = cleaned
    if not cleaned:
        result["highest_month"] = None
        result["lowest_month"] = None
        return
    comparable = [row for row in cleaned if not row.get("partial_tracking") and not row.get("partial_current")]
    extrema = comparable or cleaned
    result["highest_month"] = max(extrema, key=lambda row: row["value"])
    result["lowest_month"] = min(extrema, key=lambda row: row["value"])


def _comparison_coverage_reason(transactions: pd.DataFrame, comp_start: date, comp_end: date) -> str | None:
    first = tracking_start_date(transactions)
    if first is None or comp_start >= first:
        return None
    if comp_end < first:
        return f"对比区间 {comp_start}–{comp_end} 完全早于账本开始追踪日期 {first}。"
    return f"对比区间 {comp_start}–{comp_end} 在 {first} 之前缺少账本覆盖，不能把未追踪日期当作 0。"


def execute_finance_plan(plan: base.FinanceQueryPlan, transactions: pd.DataFrame) -> dict:
    result = base.execute_finance_plan(plan, transactions)
    aggregation = plan.aggregation or "amount"
    flow = plan.flow or "expense"
    start = base._parse_iso(plan.date_from) or date(today_my().year, 1, 1)
    end = min(base._parse_iso(plan.date_to) or date(today_my().year, 12, 31), today_my())
    first = tracking_start_date(transactions)

    _sanitize_monthly_coverage(result, transactions)

    if aggregation == "average_month":
        result["authoritative_total"] = _completed_month_average(plan, transactions, start, end, flow)
        if first is not None and start < first:
            result["coverage_note"] = f"每月平均只从账本实际追踪开始日期 {first} 起计算；追踪前月份不视为 0 元。"
    elif aggregation == "average_day":
        value, effective_start, effective_end = _tracked_day_average(plan, transactions, start, end, flow)
        result["authoritative_total"] = value
        if effective_start is not None and effective_start > start:
            result["coverage_note"] = f"每日平均分母从账本实际追踪开始日期 {effective_start} 起计算，不包含追踪前日期。"

    comp_range = base._comparison_range(plan, start, end)
    if result.get("comparison") and comp_range:
        comp_start, comp_end = comp_range
        comp_end = min(comp_end, today_my())
        reason = _comparison_coverage_reason(transactions, comp_start, comp_end)
        if reason:
            result["comparison"] = None
            result["comparison_unavailable_reason"] = reason
        elif aggregation == "average_month":
            value = _completed_month_average(plan, transactions, comp_start, comp_end, flow)
            delta = round(result["authoritative_total"] - value, 2)
            result["comparison"]["value"] = value
            result["comparison"]["delta"] = delta
            result["comparison"]["percent"] = None if value <= 0 else delta / value * 100
        elif aggregation == "average_day":
            value, _, _ = _tracked_day_average(plan, transactions, comp_start, comp_end, flow)
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


def authoritative_summary_markdown(result: dict) -> str:
    text = base.authoritative_summary_markdown(result)
    notes: list[str] = []
    if result.get("coverage_note"):
        notes.append(str(result["coverage_note"]))
    if result.get("comparison_unavailable_reason"):
        notes.append("对比未计算：" + str(result["comparison_unavailable_reason"]))
    monthly = list(result.get("monthly") or [])
    if any(row.get("partial_tracking") for row in monthly):
        notes.append("† 表示账本在该月中途才开始追踪；该部分月不会参与最高／最低完整月份判断。")
    if any(row.get("partial_current") for row in monthly):
        notes.append("* 表示当前月份尚未结束；有完整月份可用时不会用当前部分月决定最高／最低月份。")
    if notes:
        text += "\n\n" + "\n\n".join(notes)
    return text


FinanceQueryPlan = base.FinanceQueryPlan
answer_finance_question = base.answer_finance_question
categorize_macro = base.categorize_macro
finance_list_frame = base.finance_list_frame
state_from_plan = base.state_from_plan
