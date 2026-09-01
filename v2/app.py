"""Stable Streamlit entry point for WY Wallet V2.

The full interface remains in ``app_rich.py``. This entry point applies a few
small compatibility adapters before executing it:

1. Transaction rows are fetched from Supabase in pages so datasets larger than
   the server's per-request row limit are displayed completely.
2. The transaction ledger order is controlled by the app's explicit sort menu,
   not accidental clicks on table headers.
3. Import interfaces have been removed; export backup remains available.
4. Every Plotly chart receives one calm visual system and locked axes, avoiding
   accidental zooming, panning, legend hiding, or fullscreen interactions.
5. Finance chat receives query-aware local aggregates/details so questions such
   as "8月油费多少" can be answered without sending the complete ledger.
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import re
import runpy

import google.generativeai as genai
import pandas as pd
import streamlit as st
import supabase as supabase_module


BATCH_SIZE = 1000
MAX_TRANSACTION_ROWS = 100_000
AI_DETAIL_LIMIT = 800
AI_AGGREGATE_LIMIT = 1000

# Inspired by the clearer palette of the original wallet, but restrained enough
# for a professional finance dashboard.
CHART_COLORS = [
    "#5B8FF9",
    "#61DDAA",
    "#F6BD16",
    "#7262FD",
    "#78D3F8",
    "#9661BC",
    "#F6903D",
    "#E8684A",
    "#6DC8EC",
    "#9270CA",
]
SEMANTIC_COLORS = {
    "收入": "#35B77E",
    "Income": "#35B77E",
    "支出": "#EF6464",
    "Expense": "#EF6464",
    "结余": "#F6BD16",
    "Balance": "#F6BD16",
}
LOCKED_CHART_CONFIG = {
    "displayModeBar": False,
    "displaylogo": False,
    "scrollZoom": False,
    "doubleClick": False,
    "showTips": False,
    "editable": False,
    "responsive": True,
}
CHART_CSS = """
<style>
[data-testid="stPlotlyChart"] {
    border: 1px solid var(--wy-border, rgba(128,128,128,.22));
    border-radius: 16px;
    padding: .35rem .45rem .1rem;
    background:
        linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
    box-shadow: 0 8px 28px rgba(0,0,0,.08);
    overflow: hidden;
}
[data-testid="stPlotlyChart"] [data-testid="stElementToolbar"] {
    display: none !important;
}
[data-testid="stPlotlyChart"] .js-plotly-plot,
[data-testid="stPlotlyChart"] .plot-container {
    touch-action: pan-y !important;
}
@media (max-width: 760px) {
    [data-testid="stPlotlyChart"] {
        border-radius: 12px;
        padding: .15rem .1rem 0;
        box-shadow: none;
    }
}
</style>
"""


class _QueryProxy:
    """Record a Supabase query chain and replay it with pagination when needed."""

    def __init__(self, client, table_name: str, calls=None, operation: str | None = None):
        self._client = client
        self._table_name = table_name
        self._calls = list(calls or [])
        self._operation = operation

    def _append(self, name, args, kwargs, operation=None):
        return _QueryProxy(
            self._client,
            self._table_name,
            self._calls + [(name, args, kwargs)],
            operation or self._operation,
        )

    def select(self, *args, **kwargs):
        return self._append("select", args, kwargs, "select")

    def insert(self, *args, **kwargs):
        return self._append("insert", args, kwargs, "insert")

    def update(self, *args, **kwargs):
        return self._append("update", args, kwargs, "update")

    def delete(self, *args, **kwargs):
        return self._append("delete", args, kwargs, "delete")

    def upsert(self, *args, **kwargs):
        return self._append("upsert", args, kwargs, "upsert")

    def __getattr__(self, name):
        def chained(*args, **kwargs):
            return self._append(name, args, kwargs)

        return chained

    def _build(self, page_range=None):
        builder = self._client.table(self._table_name)
        for name, args, kwargs in self._calls:
            builder = getattr(builder, name)(*args, **kwargs)
        if page_range is not None:
            builder = builder.range(page_range[0], page_range[1])
        return builder

    def execute(self):
        # The app's main transaction query previously returned only the newest
        # server-limited page. Replay transaction SELECTs in 1,000-row pages.
        has_explicit_range = any(name == "range" for name, _, _ in self._calls)
        if self._table_name != "transactions" or self._operation != "select" or has_explicit_range:
            return self._build().execute()

        all_rows = []
        first_response = None
        offset = 0
        while offset < MAX_TRANSACTION_ROWS:
            response = self._build((offset, offset + BATCH_SIZE - 1)).execute()
            if first_response is None:
                first_response = response
            rows = list(getattr(response, "data", None) or [])
            all_rows.extend(rows)
            if len(rows) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        # Persist one normalized source for query-aware AI context. This is only
        # kept inside the Streamlit session and is never sent wholesale to AI.
        st.session_state["_wy_transaction_rows"] = all_rows

        return SimpleNamespace(
            data=all_rows,
            count=getattr(first_response, "count", None),
            status_code=getattr(first_response, "status_code", None),
        )


class _ClientProxy:
    def __init__(self, client):
        self._client = client

    def table(self, table_name: str):
        return _QueryProxy(self._client, table_name)

    def __getattr__(self, name):
        return getattr(self._client, name)


_original_create_client = getattr(
    supabase_module.create_client,
    "_wy_original",
    supabase_module.create_client,
)


def _paginated_create_client(*args, **kwargs):
    return _ClientProxy(_original_create_client(*args, **kwargs))


_paginated_create_client._wy_original = _original_create_client


def _fetch_transaction_rows_for_ai() -> list[dict]:
    """Return all rows locally, refreshing from Supabase only when necessary."""
    cached = st.session_state.get("_wy_transaction_rows")
    if isinstance(cached, list) and cached:
        return cached

    client = _original_create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    rows: list[dict] = []
    offset = 0
    while offset < MAX_TRANSACTION_ROWS:
        response = (
            client.table("transactions")
            .select("date,item,category,type,amount,note")
            .order("date", desc=True)
            .range(offset, offset + BATCH_SIZE - 1)
            .execute()
        )
        batch = list(getattr(response, "data", None) or [])
        rows.extend(batch)
        if len(batch) < BATCH_SIZE:
            break
        offset += BATCH_SIZE
    st.session_state["_wy_transaction_rows"] = rows
    return rows


_CHINESE_MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def _resolve_question_scope(question: str, selected_year: int) -> tuple[int, int | None, int | None]:
    """Resolve explicit/relative year, month and day from a Chinese finance question."""
    text = str(question or "").strip()
    now = datetime.now()
    year = int(selected_year)

    explicit_year = re.search(r"(?<!\d)(20\d{2})(?:年)?", text)
    if explicit_year:
        year = int(explicit_year.group(1))
    elif "前年" in text:
        year = now.year - 2
    elif "去年" in text:
        year = now.year - 1
    elif "今年" in text:
        year = now.year

    month = None
    numeric_month = re.search(r"(?<!\d)(1[0-2]|0?[1-9])\s*月", text)
    if numeric_month:
        month = int(numeric_month.group(1))
    else:
        chinese_month = re.search(r"(十二|十一|十|[一二三四五六七八九])月", text)
        if chinese_month:
            month = _CHINESE_MONTHS[chinese_month.group(1)]
        elif any(token in text for token in ["本月", "这个月", "這個月", "今月"]):
            year, month = now.year, now.month
        elif any(token in text for token in ["上月", "上个月", "上個月"]):
            if now.month == 1:
                year, month = now.year - 1, 12
            else:
                year, month = now.year, now.month - 1

    day = None
    day_match = re.search(r"(?<!\d)(3[01]|[12]?\d)\s*(?:日|号|號)", text)
    if day_match and month is not None:
        day = int(day_match.group(1))

    return year, month, day


def _records(frame: pd.DataFrame, columns: list[str], limit: int) -> list[dict]:
    if frame.empty:
        return []
    clean = frame[columns].head(limit).copy()
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.strftime("%Y-%m-%d")
    return clean.to_dict("records")


def _build_query_aware_context(question: str, selected_year: int) -> dict:
    """Build local aggregates plus only the detail slice relevant to the question."""
    rows = _fetch_transaction_rows_for_ai()
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"resolved_scope": {"year": int(selected_year)}, "available": False}

    defaults = {"item": "未知", "category": "其他", "type": "Expense", "amount": 0.0, "note": ""}
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    frame["item"] = frame["item"].fillna("未知").astype(str).str.strip()
    frame["category"] = frame["category"].fillna("其他").astype(str).str.strip()
    frame["note"] = frame["note"].fillna("").astype(str).str.strip().str.slice(0, 100)
    frame = frame.dropna(subset=["date"])

    year, month, day = _resolve_question_scope(question, selected_year)
    year_rows = frame[frame["date"].dt.year == int(year)].copy()
    year_expenses = year_rows[year_rows["type"] == "Expense"].copy()

    scope_rows = year_rows.copy()
    if month is not None:
        scope_rows = scope_rows[scope_rows["date"].dt.month == int(month)]
    if day is not None:
        scope_rows = scope_rows[scope_rows["date"].dt.day == int(day)]

    scope_expenses = scope_rows[scope_rows["type"] == "Expense"].copy()
    scope_income = scope_rows[scope_rows["type"] == "Income"].copy()

    monthly_category = pd.DataFrame(columns=["month", "category", "amount", "count"])
    monthly_item = pd.DataFrame(columns=["month", "item", "category", "amount", "count"])
    year_item = pd.DataFrame(columns=["item", "category", "amount", "count"])
    if not year_expenses.empty:
        year_expenses = year_expenses.assign(month=year_expenses["date"].dt.month)
        monthly_category = (
            year_expenses.groupby(["month", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values(["month", "amount"], ascending=[True, False])
        )
        monthly_item = (
            year_expenses.groupby(["month", "item", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values(["month", "amount"], ascending=[True, False])
        )
        year_item = (
            year_expenses.groupby(["item", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values("amount", ascending=False)
        )

    for aggregate in [monthly_category, monthly_item, year_item]:
        if "amount" in aggregate.columns:
            aggregate["amount"] = aggregate["amount"].round(2)

    scope_category = (
        scope_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).round(2).to_dict()
        if not scope_expenses.empty else {}
    )
    scope_item = (
        scope_expenses.groupby(["item", "category"])["amount"]
        .agg(["sum", "size"])
        .reset_index()
        .rename(columns={"sum": "amount", "size": "count"})
        .sort_values("amount", ascending=False)
        if not scope_expenses.empty else pd.DataFrame(columns=["item", "category", "amount", "count"])
    )
    if not scope_item.empty:
        scope_item["amount"] = scope_item["amount"].round(2)

    # Raw detail is only attached when the question identifies a month/day. For
    # broad annual questions Gemini receives aggregates, not the complete ledger.
    detail_records = []
    if month is not None:
        detail = scope_rows.sort_values(["date", "amount"], ascending=[True, False]).copy()
        detail["amount"] = detail["amount"].round(2)
        detail_records = _records(
            detail,
            ["date", "item", "category", "type", "amount", "note"],
            AI_DETAIL_LIMIT,
        )

    return {
        "resolved_scope": {"year": int(year), "month": month, "day": day},
        "available": True,
        "scope_totals": {
            "transaction_count": int(len(scope_rows)),
            "expense": round(float(scope_expenses["amount"].sum()), 2),
            "income": round(float(scope_income["amount"].sum()), 2),
        },
        "scope_category_expense": scope_category,
        "scope_item_expense": _records(scope_item, ["item", "category", "amount", "count"], AI_AGGREGATE_LIMIT),
        "monthly_category_expense": _records(monthly_category, ["month", "category", "amount", "count"], AI_AGGREGATE_LIMIT),
        "monthly_item_expense": _records(monthly_item, ["month", "item", "category", "amount", "count"], AI_AGGREGATE_LIMIT),
        "year_item_expense": _records(year_item, ["item", "category", "amount", "count"], AI_AGGREGATE_LIMIT),
        "question_scoped_transactions": detail_records,
        "detail_truncated": bool(month is not None and len(scope_rows) > AI_DETAIL_LIMIT),
    }


def _extract_finance_question(prompt: str) -> tuple[str, int] | None:
    if not isinstance(prompt, str) or "你是私人财务分析助手" not in prompt or "问题：" not in prompt:
        return None
    question = prompt.rsplit("问题：", 1)[-1].strip()
    year_match = re.search(r'"year"\s*:\s*(20\d{2})', prompt)
    selected_year = int(year_match.group(1)) if year_match else datetime.now().year
    return question, selected_year


def _enrich_finance_prompt(contents):
    parsed = _extract_finance_question(contents) if isinstance(contents, str) else None
    if parsed is None:
        return contents
    question, selected_year = parsed
    try:
        context = _build_query_aware_context(question, selected_year)
    except Exception as exc:
        # Keep the original chat functional even if local retrieval has a transient issue.
        return contents + f"\n\n本地检索状态：失败（{type(exc).__name__}）。只使用上面的基础汇总回答。"

    instruction = """

【本地问题相关账本检索】
下面资料由应用在本地账本中即时计算，优先级高于之前对话中的旧回答。
不要因为基础年度摘要没有某个项目，就回答“资料没有提供”；请先检查这里的项目聚合与问题范围明细。
用户用词与项目名称不必完全相同，应按财务语义判断。例如“油费/打油/加油/petrol/fuel/汽油”可视为同一消费概念，但必须只合计资料中真实存在、语义确实相关的项目。
若需要语义归类后合计，请简短说明包含了哪些项目名称、多少笔，并给出 RM 两位小数结果。
若问题指定月份/日期，必须以 resolved_scope 和 question_scoped_transactions 为准，不可拿全年数字代替。
如果确实找不到语义相关记录，再明确说该范围内未找到，而不是说系统没有提供明细。
资料：
"""
    return contents + instruction + json.dumps(context, ensure_ascii=False, default=str)


_original_generative_model = getattr(genai.GenerativeModel, "_wy_original", genai.GenerativeModel)


class _FinanceAwareGenerativeModel:
    """Transparent Gemini wrapper that enriches only WY Wallet finance-chat prompts."""

    def __init__(self, *args, **kwargs):
        self._delegate = _original_generative_model(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def generate_content(self, contents, *args, **kwargs):
        return self._delegate.generate_content(_enrich_finance_prompt(contents), *args, **kwargs)


_FinanceAwareGenerativeModel._wy_original = _original_generative_model


# Keep references to the real Streamlit functions so wrappers do not stack on
# reruns when Streamlit reuses modules.
_original_dataframe = getattr(st.dataframe, "_wy_original", st.dataframe)
_original_plotly_chart = getattr(st.plotly_chart, "_wy_original", st.plotly_chart)
_original_file_uploader = getattr(st.file_uploader, "_wy_original", st.file_uploader)
_original_tabs = getattr(st.tabs, "_wy_original", st.tabs)
_original_warning = getattr(st.warning, "_wy_original", st.warning)
_original_markdown = getattr(st.markdown, "_wy_original", st.markdown)
_chart_css_injected = False


def _stable_dataframe(*args, **kwargs):
    if kwargs.get("key") == "transaction_table":
        # Column selection disables built-in header sorting while row selection
        # still opens transaction details.
        kwargs["selection_mode"] = ["single-row", "single-column"]
    return _original_dataframe(*args, **kwargs)


def _polish_figure(fig):
    """Apply a consistent, readable and non-zoomable finance-chart style."""
    if not hasattr(fig, "update_layout") or not hasattr(fig, "data"):
        return fig

    trace_types = {getattr(trace, "type", "") for trace in fig.data}
    horizontal_bar = any(
        getattr(trace, "type", "") == "bar"
        and getattr(trace, "orientation", None) == "h"
        for trace in fig.data
    )
    pie_only = bool(trace_types) and trace_types.issubset({"pie"})

    fig.update_layout(
        colorway=CHART_COLORS,
        dragmode=False,
        clickmode="none",
        uirevision="wy-wallet-fixed-chart",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=18, r=18, t=34, b=18),
        font=dict(size=13),
        hoverlabel=dict(
            bgcolor="rgba(20,24,33,.96)",
            bordercolor="rgba(255,255,255,.16)",
            font=dict(color="#FFFFFF", size=12),
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
            font=dict(size=11),
            itemclick=False,
            itemdoubleclick=False,
        ),
        transition=dict(duration=180, easing="cubic-in-out"),
    )

    if pie_only:
        fig.update_layout(hovermode="closest")
    elif getattr(fig.layout, "hovermode", None) in (None, False):
        fig.update_layout(hovermode="x unified")

    # Fixed axes stop mouse drags, double-clicks and mobile touches from changing
    # the visible range. Subtle grids keep amounts easy to compare.
    try:
        fig.update_xaxes(
            fixedrange=True,
            automargin=True,
            showgrid=horizontal_bar,
            gridcolor="rgba(148,163,184,.13)",
            griddash="dot",
            zeroline=False,
            tickfont=dict(size=11),
            title_font=dict(size=12),
        )
        fig.update_yaxes(
            fixedrange=True,
            automargin=True,
            showgrid=not horizontal_bar,
            gridcolor="rgba(148,163,184,.13)",
            griddash="dot",
            zeroline=False,
            tickfont=dict(size=11),
            title_font=dict(size=12),
        )
    except Exception:
        pass

    # Use fixed financial semantics for cash-flow series while category series
    # continue through the restrained multi-category palette.
    for trace in fig.data:
        semantic = SEMANTIC_COLORS.get(str(getattr(trace, "name", "")))
        if semantic:
            try:
                if getattr(trace, "type", "") in {"scatter", "line"}:
                    trace.update(line=dict(color=semantic, width=3), marker=dict(color=semantic, size=7))
                else:
                    trace.update(marker_color=semantic)
            except Exception:
                pass

    try:
        fig.update_traces(
            marker_line_width=0,
            opacity=.94,
            cliponaxis=False,
            selector=dict(type="bar"),
        )
        # Plotly 6 supports rounded bar corners. Ignore gracefully on older builds.
        fig.update_traces(marker_cornerradius=7, selector=dict(type="bar"))
    except Exception:
        pass

    try:
        fig.update_traces(
            line=dict(width=3),
            marker=dict(size=7),
            selector=dict(type="scatter"),
        )
    except Exception:
        pass

    try:
        fig.update_traces(
            marker=dict(line=dict(color="rgba(255,255,255,.18)", width=1.5)),
            textfont=dict(size=12),
            pull=0,
            selector=dict(type="pie"),
        )
    except Exception:
        pass

    return fig


def _fixed_plotly_chart(figure_or_data, *args, **kwargs):
    figure_or_data = _polish_figure(figure_or_data)
    supplied = dict(kwargs.get("config") or {})
    supplied.update(LOCKED_CHART_CONFIG)
    kwargs["config"] = supplied
    return _original_plotly_chart(figure_or_data, *args, **kwargs)


def _without_import_uploader(label, *args, **kwargs):
    if kwargs.get("key") == "import_file":
        return None
    return _original_file_uploader(label, *args, **kwargs)


def _backup_only_tabs(labels, *args, **kwargs):
    if list(labels) == ["类别管理", "备份与导入"]:
        labels = ["类别管理", "备份"]
    return _original_tabs(labels, *args, **kwargs)


def _without_import_warning(body, *args, **kwargs):
    if isinstance(body, str) and body.startswith("导入只会新增"):
        return None
    return _original_warning(body, *args, **kwargs)


def _clean_settings_copy(body, *args, **kwargs):
    global _chart_css_injected
    if isinstance(body, str):
        body = body.replace(
            "管理类别、导出备份，以及安全导入历史数据。",
            "管理类别并导出备份。",
        )
        body = body.replace(
            "只发送年度汇总、类别统计与最高金额记录，不发送完整账本。",
            "金额先由本地账本检索和聚合；AI 只接收与当前问题相关的汇总／明细，不发送整本账本。",
        )
        # The first app stylesheet is the safest place to append chart-card CSS,
        # after set_page_config has already run.
        if not _chart_css_injected and "<style>" in body:
            body = body + CHART_CSS
            _chart_css_injected = True
    return _original_markdown(body, *args, **kwargs)


_stable_dataframe._wy_original = _original_dataframe
_fixed_plotly_chart._wy_original = _original_plotly_chart
_without_import_uploader._wy_original = _original_file_uploader
_backup_only_tabs._wy_original = _original_tabs
_without_import_warning._wy_original = _original_warning
_clean_settings_copy._wy_original = _original_markdown

supabase_module.create_client = _paginated_create_client
genai.GenerativeModel = _FinanceAwareGenerativeModel
st.dataframe = _stable_dataframe
st.plotly_chart = _fixed_plotly_chart
st.file_uploader = _without_import_uploader
st.tabs = _backup_only_tabs
st.warning = _without_import_warning
st.markdown = _clean_settings_copy

# Old chat turns were created with the previous coarse-only context and can
# anchor Gemini to a wrong "data not provided" answer. Clear them once after
# this context-engine upgrade, then preserve history normally on future reruns.
if not st.session_state.get("_wy_ai_context_v2_ready"):
    st.session_state.pop("ai_chat_history", None)
    st.session_state["_wy_ai_context_v2_ready"] = True

try:
    runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
finally:
    supabase_module.create_client = _original_create_client
    genai.GenerativeModel = _original_generative_model
    st.dataframe = _original_dataframe
    st.plotly_chart = _original_plotly_chart
    st.file_uploader = _original_file_uploader
    st.tabs = _original_tabs
    st.warning = _original_warning
    st.markdown = _original_markdown
