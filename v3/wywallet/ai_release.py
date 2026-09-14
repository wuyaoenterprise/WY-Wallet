from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from . import ai as base
from .config import EXPENSE, INCOME, REFUND, today_my
from .product_logic import tracking_start_date


# These aliases are intentionally ledger-oriented rather than generic language
# guesses.  In particular, "all food-related" should include the user's food
# categories even when an item name itself does not contain words like 餐/food.
_LOCAL_SEMANTIC_GROUPS: list[tuple[list[str], list[str]]] = [
    (
        ["油费", "油費", "打油", "加油", "petrol", "fuel", "汽油", "车油", "車油"],
        [
            "打油", "加油", "petrol", "fuel", "汽油", "油费", "油費", "车油", "車油",
            "shell", "petronas", "caltex", "petron", "bhp",
        ],
    ),
    (
        ["餐饮", "餐飲", "吃饭", "吃飯", "吃的", "吃相關", "吃相关", "食物", "food", "lunch", "dinner", "breakfast"],
        [
            # Food categories used in the ledger.
            "饮食", "飲食", "早餐", "午餐", "晚餐", "宵夜", "饮料", "飲料", "面包", "麵包",
            "水果", "蔬菜", "肉类", "肉類", "零食", "餐饮", "餐飲", "食材",
            # Common item/name patterns.
            "餐", "饭", "飯", "粉", "面", "麵", "food", "lunch", "dinner", "breakfast",
            "mcd", "mcdonald", "kfc", "starbucks", "thai food",
        ],
    ),
]

_CANONICAL_SUBJECT_GROUPS: list[tuple[str, list[str]]] = [
    ("加油", ["油费", "油費", "打油", "加油", "petrol", "fuel", "汽油", "车油", "車油"]),
    ("餐饮", ["餐饮", "餐飲", "吃饭", "吃飯", "吃的", "吃相关", "吃相關", "食物", "food", "lunch", "dinner", "breakfast"]),
]


def _local_subject_matches(subject: str, frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    subject_text = base._normalize_text(subject)
    aliases: list[str] = []
    for triggers, candidates in [*_LOCAL_SEMANTIC_GROUPS, *base.SEMANTIC_GROUPS]:
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


def _canonical_subjects_from_question(question: str) -> list[str]:
    text = base._normalize_text(question)
    compact = re.sub(r"\s+", "", text)
    found: list[str] = []
    for canonical, triggers in _CANONICAL_SUBJECT_GROUPS:
        if any(base._alias_match(compact, trigger) for trigger in triggers):
            found.append(canonical)
    return list(dict.fromkeys(found))


def _requests_monthly(question: str) -> bool:
    compact = re.sub(r"\s+", "", str(question or "").casefold())
    return any(token in compact for token in [
        "每月", "每个月", "每個月", "按月", "月度", "月份", "月分布", "月分佈", "monthly",
    ])


def _requests_split(question: str) -> bool:
    compact = re.sub(r"\s+", "", str(question or "").casefold())
    return any(token in compact for token in [
        "分开", "分開", "拆开", "拆開", "分别", "分別", "各自", "各別", "独立", "獨立",
    ])


def _resolve_direct_range(question: str, selected_year: int, state: dict) -> tuple[date, date]:
    compact = re.sub(r"\s+", "", str(question or "").casefold())
    today = today_my()

    year_match = re.search(r"(20\d{2})\s*年?", compact)
    explicit_year = int(year_match.group(1)) if year_match else None
    year = explicit_year or int(selected_year)

    if any(token in compact for token in ["本月", "这个月", "這個月", "今月"]):
        if explicit_year is None:
            year = today.year
        month_match = re.search(r"(\d{1,2})月", compact)
        month = int(month_match.group(1)) if month_match else today.month
        start = date(year, month, 1)
        end = min((pd.Timestamp(start) + pd.offsets.MonthEnd(0)).date(), today)
        return start, end

    has_explicit_time = bool(explicit_year or any(token in compact for token in [
        "今年", "本年", "全年", "整年", "本月", "这个月", "這個月",
    ]))
    if not has_explicit_time and state.get("date_from") and state.get("date_to"):
        start = base._parse_iso(state.get("date_from"))
        end = base._parse_iso(state.get("date_to"))
        if start and end:
            return start, min(end, today)

    start = date(year, 1, 1)
    end = date(year, 12, 31)
    if year == today.year:
        end = min(end, today)
    return start, end


def _subject_plan(subject: str, start: date, end: date, *, intent: str = "trend") -> base.FinanceQueryPlan:
    return base.FinanceQueryPlan(
        intent=intent,
        subject_mode="specific",
        subject=subject,
        aggregation_mode="specific",
        aggregation="amount",
        flow_mode="specific",
        flow="expense",
        time_mode="specific",
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        comparison="none",
    )


def try_local_finance_answer(
    question: str,
    selected_year: int,
    transactions: pd.DataFrame,
    conversation_state: dict | None,
) -> dict | None:
    """Answer clear multi-subject monthly requests without spending a Gemini call.

    The generic FinanceQueryPlan intentionally represents one subject at a time.
    A request such as "加油和餐饮分开看，每月分布" therefore needs a deterministic
    multi-series path instead of asking the LLM to squeeze both subjects into one
    filter.  This also makes terse follow-ups like "我要看每月分布" reliable.
    """
    state = conversation_state or {}
    explicit_subjects = _canonical_subjects_from_question(question)
    remembered_subjects = [str(v) for v in state.get("multi_subjects", []) if str(v).strip()]
    subjects = explicit_subjects or remembered_subjects

    should_handle = (
        len(subjects) >= 2
        and (
            _requests_monthly(question)
            or _requests_split(question)
            or (remembered_subjects and not explicit_subjects)
        )
    )
    if not should_handle:
        return None

    start, end = _resolve_direct_range(question, selected_year, state)
    series: dict[str, dict] = {}
    labels: list[str] = []
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    match_details: dict[str, dict[str, list[str]]] = {}

    for subject in subjects:
        plan = _subject_plan(subject, start, end)
        items, categories = _local_subject_matches(subject, transactions)
        plan.matched_items = items
        plan.matched_categories = categories
        result = execute_finance_plan(plan, transactions)
        month_map = {str(row["label"]): row for row in result.get("monthly", [])}
        series[subject] = month_map
        for label in month_map:
            if label not in labels:
                labels.append(label)
        totals[subject] = float(result.get("authoritative_total") or 0)
        counts[subject] = int(result.get("transaction_count") or 0)
        match_details[subject] = {
            "items": list(result.get("matched_items") or []),
            "categories": list(result.get("matched_categories") or []),
        }

    labels.sort()
    header = "| 月份 | " + " | ".join(f"{subject}净支出" for subject in subjects) + " |"
    divider = "| --- | " + " | ".join("---:" for _ in subjects) + " |"
    rows = [header, divider]
    today = today_my()
    for label in labels:
        display = label
        if label == f"{today.year:04d}-{today.month:02d}" and end == today:
            display += f"（截至{today.day}日）"
        values = [f"RM {float(series[subject].get(label, {}).get('value', 0) or 0):,.2f}" for subject in subjects]
        rows.append("| " + display + " | " + " | ".join(values) + " |")

    total_line = "；".join(
        f"{subject}合计 **RM {totals[subject]:,.2f}**（{counts[subject]} 笔）" for subject in subjects
    )
    scope_parts: list[str] = []
    if "餐饮" in subjects:
        food_categories = match_details["餐饮"].get("categories") or []
        if food_categories:
            scope_parts.append("餐饮已按所有吃相关类别统计：" + "、".join(food_categories))
    if "加油" in subjects:
        fuel_categories = match_details["加油"].get("categories") or []
        if fuel_categories:
            scope_parts.append("加油范围：" + "、".join(fuel_categories))

    markdown = (
        f"**本地精确结果｜分开统计 · {start.isoformat()} ～ {end.isoformat()}**\n\n"
        + "\n".join(rows)
        + "\n\n"
        + total_line
    )
    if scope_parts:
        markdown += "\n\n" + "；".join(scope_parts)

    next_state = {
        **state,
        "multi_subjects": subjects,
        "subject": None,
        "matched_items": [],
        "matched_categories": [],
        "aggregation": "amount",
        "flow": "expense",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "comparison": "none",
        "comparison_date_from": None,
        "comparison_date_to": None,
        "intent": "trend",
    }
    return {"markdown": markdown, "state": next_state}


def _local_common_plan(
    question: str,
    selected_year: int,
    transactions: pd.DataFrame,
    conversation_state: dict | None,
) -> base.FinanceQueryPlan | None:
    """Resolve high-confidence common finance questions without Gemini.

    This is deliberately conservative.  Ambiguous questions still use Gemini,
    while routine month comparison/trend follow-ups remain available even when
    the free Gemini quota is exhausted.
    """
    state = conversation_state or {}
    compact = re.sub(r"\s+", "", str(question or "").casefold())
    today = today_my()
    subjects = _canonical_subjects_from_question(question)

    current_month = any(token in compact for token in ["本月", "这个月", "這個月"])
    previous_month = any(token in compact for token in ["上个月", "上個月", "上月"])
    asks_why = any(token in compact for token in ["为什么", "為什麼", "原因", "解释", "解釋", "怎么", "怎麼"])

    if current_month and previous_month:
        start = date(today.year, today.month, 1)
        end = today
        subject = subjects[0] if len(subjects) == 1 else None
        plan = base.FinanceQueryPlan(
            intent="explain" if asks_why else "compare",
            subject_mode="specific" if subject else "all",
            subject=subject,
            aggregation_mode="specific",
            aggregation="amount",
            flow_mode="specific",
            flow="expense",
            time_mode="specific",
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            comparison="previous_month",
        )
        return plan

    # Follow-ups such as "上个月也有这些开销啊" should keep the original
    # primary period/subject and inspect the already-defined comparison locally.
    if previous_month and state.get("date_from") and state.get("date_to") and any(
        token in compact for token in ["也有", "都有", "这些", "這些", "不是也", "一樣", "一样"]
    ):
        plan = base.FinanceQueryPlan(
            intent="explain",
            subject_mode="specific" if state.get("subject") else "all",
            subject=state.get("subject"),
            aggregation_mode="specific",
            aggregation=str(state.get("aggregation") or "amount"),
            flow_mode="specific",
            flow=str(state.get("flow") or "expense"),
            time_mode="specific",
            date_from=str(state.get("date_from")),
            date_to=str(state.get("date_to")),
            comparison="previous_month",
        )
        plan.matched_items = list(state.get("matched_items") or [])
        plan.matched_categories = list(state.get("matched_categories") or [])
        return plan

    # Single-subject monthly trends are also deterministic and do not need LLM
    # planning. Multi-subject trends are handled by try_local_finance_answer.
    if _requests_monthly(question):
        subject = subjects[0] if len(subjects) == 1 else None
        if subject is None and state.get("subject") and not state.get("multi_subjects"):
            subject = str(state.get("subject"))
        if subject:
            start, end = _resolve_direct_range(question, selected_year, state)
            return _subject_plan(subject, start, end, intent="trend")

    return None


def plan_finance_question(question: str, selected_year: int, transactions: pd.DataFrame,
                          conversation_state: dict | None, recent_history: list[dict] | None) -> base.FinanceQueryPlan:
    local = _local_common_plan(question, selected_year, transactions, conversation_state)
    plan = local or base.plan_finance_question(question, selected_year, transactions, conversation_state, recent_history)
    if plan.subject_mode == "specific" and plan.subject:
        items, categories = _local_subject_matches(plan.subject, transactions)
        plan.matched_items = list(dict.fromkeys([*plan.matched_items, *items]))
        plan.matched_categories = list(dict.fromkeys([*plan.matched_categories, *categories]))
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

    if effective_start.day > 1:
        next_month = (pd.Timestamp(effective_start).replace(day=1) + pd.DateOffset(months=1)).date()
        if next_month <= effective_end:
            effective_start = next_month

    ranged = base._filter_range(base_frame, effective_start, effective_end)
    total = base._amount_total(ranged, flow)
    months = (effective_end.year - effective_start.year) * 12 + effective_end.month - effective_start.month + 1
    return round(total / max(months, 1), 2)


def _effect_frame(frame: pd.DataFrame, flow: str) -> pd.DataFrame:
    if frame.empty:
        work = frame.copy()
        work["effect"] = pd.Series(dtype=float)
        return work
    work = frame.copy()
    if flow == "expense":
        work = work[work["type"].isin([EXPENSE, REFUND])].copy()
        work["effect"] = work["amount"].where(work["type"] == EXPENSE, -work["amount"])
    elif flow == "income":
        work = work[work["type"] == INCOME].copy()
        work["effect"] = work["amount"]
    elif flow == "refund":
        work = work[work["type"] == REFUND].copy()
        work["effect"] = work["amount"]
    elif flow == "net":
        work["effect"] = work.apply(
            lambda row: float(row["amount"]) if row["type"] in [INCOME, REFUND] else -float(row["amount"]), axis=1
        )
    else:
        work["effect"] = work["amount"].astype(float)
    return work


def _driver_table(current: pd.DataFrame, previous: pd.DataFrame, keys: list[str], limit: int = 12) -> list[dict]:
    def grouped(frame: pd.DataFrame, column_name: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=[*keys, column_name])
        return frame.groupby(keys, dropna=False)["effect"].sum().reset_index().rename(columns={"effect": column_name})

    merged = grouped(current, "current").merge(grouped(previous, "previous"), on=keys, how="outer").fillna(0)
    if merged.empty:
        return []
    merged["delta"] = merged["current"].astype(float) - merged["previous"].astype(float)
    merged = merged[merged["delta"].abs() >= 0.005].copy()
    if merged.empty:
        return []
    merged["abs_delta"] = merged["delta"].abs()
    merged = merged.sort_values(["abs_delta", "delta"], ascending=[False, False]).head(limit)
    for column in ["current", "previous", "delta"]:
        merged[column] = merged[column].astype(float).round(2)
    return merged.drop(columns=["abs_delta"]).to_dict("records")


def _attach_comparison_drivers(plan: base.FinanceQueryPlan, transactions: pd.DataFrame, result: dict) -> None:
    comparison = result.get("comparison")
    aggregation = plan.aggregation or "amount"
    if not comparison or aggregation not in {"amount", "average_day", "average_month"}:
        return

    start = base._parse_iso(result.get("date_from"))
    end = base._parse_iso(result.get("date_to"))
    comp_start = base._parse_iso(comparison.get("date_from"))
    comp_end = base._parse_iso(comparison.get("date_to"))
    if not start or not end or not comp_start or not comp_end:
        return

    filtered, _, _ = base._filter_subject(transactions.copy(), plan)
    current = base._filter_range(filtered, start, end)
    previous = base._filter_range(filtered, comp_start, comp_end)
    current_effect = _effect_frame(current, plan.flow or "expense")
    previous_effect = _effect_frame(previous, plan.flow or "expense")

    result["comparison_drivers"] = {
        "categories": _driver_table(current_effect, previous_effect, ["category"], 10),
        "items": _driver_table(current_effect, previous_effect, ["item", "category"], 15),
    }


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

    _attach_comparison_drivers(plan, transactions, result)
    return result


def _format_driver(row: dict, key: str) -> str:
    name = str(row.get(key) or "其他")
    delta = float(row.get("delta") or 0)
    current = float(row.get("current") or 0)
    previous = float(row.get("previous") or 0)
    return f"{name} {delta:+,.2f}（本期 {current:,.2f} / 上期 {previous:,.2f}）"


def _local_comparison_explanation(result: dict) -> str:
    comparison = result.get("comparison") or {}
    delta = float(comparison.get("delta") or 0)
    drivers = result.get("comparison_drivers") or {}
    categories = list(drivers.get("categories") or [])
    items = list(drivers.get("items") or [])

    if abs(delta) < 0.005:
        return "两期净支出几乎没有变化；固定项目即使金额很大，只要两期相同，对差额的贡献就是 RM 0。"

    sign = 1 if delta > 0 else -1
    direction = "增加" if delta > 0 else "减少"
    primary_categories = [row for row in categories if float(row.get("delta") or 0) * sign > 0][:4]
    offsets = [row for row in categories if float(row.get("delta") or 0) * sign < 0][:3]
    primary_items = [row for row in items if float(row.get("delta") or 0) * sign > 0][:5]

    lines = [
        f"真正要看的是两期**差额**，不是本期金额最大的项目。本期相对上期净支出{direction} **RM {abs(delta):,.2f}**。"
    ]
    if primary_categories:
        lines.append("按类别看，主要差异来自：" + "；".join(_format_driver(row, "category") for row in primary_categories) + "。")
    if primary_items:
        lines.append("按具体项目看，贡献最大的变化是：" + "；".join(_format_driver(row, "item") for row in primary_items) + "。")
    if offsets:
        lines.append("同时有这些项目在抵消变化：" + "；".join(_format_driver(row, "category") for row in offsets) + "。")
    lines.append("像房租、车贷这类固定支出，如果上月同期也有且金额相同，它们的差额就是 RM 0，不应被当成上涨原因。")
    return "\n\n".join(lines)


def answer_finance_question(question: str, result: dict) -> str:
    plan = result.get("plan") or {}
    intent = plan.get("intent")

    # Trend numbers are already completely represented by the local monthly
    # result. Calling Gemini again adds cost/quota usage without adding authority.
    if intent == "trend":
        return ""

    if result.get("comparison") and (plan.get("aggregation") or "amount") in {"amount", "average_day", "average_month"}:
        return _local_comparison_explanation(result)

    # Only open-ended explanations that cannot be derived deterministically use
    # Gemini. base.answer_finance_question already fails soft if the provider is
    # temporarily unavailable/quota-limited.
    return base.answer_finance_question(question, result)


def authoritative_summary_markdown(result: dict) -> str:
    return base.authoritative_summary_markdown(result)


def state_from_plan(plan: base.FinanceQueryPlan, result: dict | None = None) -> dict:
    state = base.state_from_plan(plan, result)
    state["intent"] = plan.intent
    return state


FinanceQueryPlan = base.FinanceQueryPlan
categorize_macro = base.categorize_macro
finance_list_frame = base.finance_list_frame
