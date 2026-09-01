"""Stable Streamlit entry point for WY Wallet V2.

The full UI stays in ``app_rich.py``. This entry point applies cross-cutting
adapters before executing it:

- paginate Supabase transaction reads so old rows are never hidden;
- keep transaction-table sorting under the app's explicit controls;
- lock and polish every Plotly chart;
- keep removed historical-import UI hidden;
- give finance chat the complete compact ledger for the selected year, plus
  deterministic local aggregates, so natural-language questions can use all
  real data instead of a coarse annual summary.
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
MAX_AI_LEDGER_ROWS = 12_000

CHART_COLORS = [
    "#5B8FF9", "#61DDAA", "#F6BD16", "#7262FD", "#78D3F8",
    "#9661BC", "#F6903D", "#E8684A", "#6DC8EC", "#9270CA",
]
SEMANTIC_COLORS = {
    "收入": "#35B77E", "Income": "#35B77E",
    "支出": "#EF6464", "Expense": "#EF6464",
    "结余": "#F6BD16", "Balance": "#F6BD16",
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
    background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.012));
    box-shadow: 0 8px 28px rgba(0,0,0,.08);
    overflow: hidden;
}
[data-testid="stPlotlyChart"] [data-testid="stElementToolbar"] {display:none !important;}
[data-testid="stPlotlyChart"] .js-plotly-plot,
[data-testid="stPlotlyChart"] .plot-container {touch-action:pan-y !important;}
@media(max-width:760px){
    [data-testid="stPlotlyChart"]{border-radius:12px;padding:.15rem .1rem 0;box-shadow:none;}
}
</style>
"""


class _QueryProxy:
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

    def select(self, *args, **kwargs): return self._append("select", args, kwargs, "select")
    def insert(self, *args, **kwargs): return self._append("insert", args, kwargs, "insert")
    def update(self, *args, **kwargs): return self._append("update", args, kwargs, "update")
    def delete(self, *args, **kwargs): return self._append("delete", args, kwargs, "delete")
    def upsert(self, *args, **kwargs): return self._append("upsert", args, kwargs, "upsert")

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

        st.session_state["_wy_transaction_rows"] = all_rows
        return SimpleNamespace(
            data=all_rows,
            count=getattr(first_response, "count", None),
            status_code=getattr(first_response, "status_code", None),
        )


class _ClientProxy:
    def __init__(self, client): self._client = client
    def table(self, table_name: str): return _QueryProxy(self._client, table_name)
    def __getattr__(self, name): return getattr(self._client, name)


_original_create_client = getattr(supabase_module.create_client, "_wy_original", supabase_module.create_client)


def _paginated_create_client(*args, **kwargs):
    return _ClientProxy(_original_create_client(*args, **kwargs))


_paginated_create_client._wy_original = _original_create_client


def _fetch_all_transactions_for_ai() -> list[dict]:
    cached = st.session_state.get("_wy_transaction_rows")
    if isinstance(cached, list):
        return cached

    client = _original_create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    rows: list[dict] = []
    offset = 0
    while offset < MAX_TRANSACTION_ROWS:
        response = (
            client.table("transactions")
            .select("date,item,category,type,amount,note")
            .order("date", desc=True)
            .order("id", desc=True)
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


def _extract_finance_question(prompt: str) -> tuple[str, int, str] | None:
    if not isinstance(prompt, str) or "你是私人财务分析助手" not in prompt or "问题：" not in prompt:
        return None
    question = prompt.rsplit("问题：", 1)[-1].strip()
    year_match = re.search(r'"year"\s*:\s*(20\d{2})', prompt)
    selected_year = int(year_match.group(1)) if year_match else datetime.now().year
    explicit_year = re.search(r"(?<!\d)(20\d{2})\s*年?", question)
    if explicit_year:
        selected_year = int(explicit_year.group(1))
    elif "去年" in question:
        selected_year = datetime.now().year - 1
    elif "前年" in question:
        selected_year = datetime.now().year - 2
    elif "今年" in question:
        selected_year = datetime.now().year

    history = ""
    if "\n对话：" in prompt:
        history = prompt.split("\n对话：", 1)[-1].rsplit("\n问题：", 1)[0].strip()
    return question, selected_year, history


def _normalize_ai_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    defaults = {"item": "未知", "category": "其他", "type": "Expense", "amount": 0.0, "note": ""}
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    frame["item"] = frame["item"].fillna("未知").astype(str).str.strip()
    frame["category"] = frame["category"].fillna("其他").astype(str).str.strip()
    frame["type"] = frame["type"].fillna("Expense").astype(str)
    frame["note"] = frame["note"].fillna("").astype(str).str.strip().str.slice(0, 120)
    return frame.dropna(subset=["date"]).sort_values("date")


def _records(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    if frame.empty:
        return []
    clean = frame[columns].copy()
    if "date" in clean.columns:
        clean["date"] = pd.to_datetime(clean["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return clean.to_dict("records")


def _build_full_ledger_context(question: str, selected_year: int) -> dict:
    frame = _normalize_ai_frame(_fetch_all_transactions_for_ai())
    if frame.empty:
        return {"year": int(selected_year), "available": False, "transactions": []}

    year_rows = frame[frame["date"].dt.year == int(selected_year)].copy()
    if len(year_rows) > MAX_AI_LEDGER_ROWS:
        ledger_rows = year_rows.head(MAX_AI_LEDGER_ROWS).copy()
        ledger_complete = False
    else:
        ledger_rows = year_rows
        ledger_complete = True

    expenses = year_rows[year_rows["type"] == "Expense"].copy()
    income = year_rows[year_rows["type"] == "Income"].copy()

    if expenses.empty:
        month_item = pd.DataFrame(columns=["month", "item", "category", "amount", "count"])
        month_category = pd.DataFrame(columns=["month", "category", "amount", "count"])
        day_item = pd.DataFrame(columns=["date", "item", "category", "amount", "count"])
    else:
        working = expenses.assign(month=expenses["date"].dt.month)
        month_item = (
            working.groupby(["month", "item", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values(["month", "amount"], ascending=[True, False])
        )
        month_category = (
            working.groupby(["month", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values(["month", "amount"], ascending=[True, False])
        )
        day_item = (
            expenses.assign(date=expenses["date"].dt.strftime("%Y-%m-%d"))
            .groupby(["date", "item", "category"])["amount"]
            .agg(["sum", "size"])
            .reset_index()
            .rename(columns={"sum": "amount", "size": "count"})
            .sort_values(["date", "amount"], ascending=[True, False])
        )
        month_item["amount"] = month_item["amount"].round(2)
        month_category["amount"] = month_category["amount"].round(2)
        day_item["amount"] = day_item["amount"].round(2)

    monthly_totals = []
    for month in range(1, 13):
        month_rows = year_rows[year_rows["date"].dt.month == month]
        monthly_totals.append({
            "month": month,
            "expense": round(float(month_rows.loc[month_rows["type"] == "Expense", "amount"].sum()), 2),
            "income": round(float(month_rows.loc[month_rows["type"] == "Income", "amount"].sum()), 2),
            "count": int(len(month_rows)),
        })

    ledger = ledger_rows[["date", "item", "category", "type", "amount", "note"]].copy()
    ledger["amount"] = ledger["amount"].round(2)

    return {
        "year": int(selected_year),
        "available": True,
        "ledger_complete": ledger_complete,
        "ledger_row_count": int(len(year_rows)),
        "totals": {
            "expense": round(float(expenses["amount"].sum()), 2),
            "income": round(float(income["amount"].sum()), 2),
        },
        "monthly_totals": monthly_totals,
        "monthly_item_expense": _records(month_item, ["month", "item", "category", "amount", "count"]),
        "monthly_category_expense": _records(month_category, ["month", "category", "amount", "count"]),
        "daily_item_expense": _records(day_item, ["date", "item", "category", "amount", "count"]),
        "transactions": _records(ledger, ["date", "item", "category", "type", "amount", "note"]),
        "user_question": question,
    }


def _enrich_finance_prompt(contents):
    parsed = _extract_finance_question(contents) if isinstance(contents, str) else None
    if parsed is None:
        return contents

    question, selected_year, history = parsed
    try:
        context = _build_full_ledger_context(question, selected_year)
    except Exception as exc:
        return f"""你是私人财务分析助手。当前完整账本读取失败：{type(exc).__name__}。
不要假装拥有未读取的数据。请告诉用户读取失败，并建议稍后重试。
问题：{question}"""

    # Do NOT append this to the old coarse prompt. The old prompt says only a
    # summary is available and can anchor the model to a false 'no detail'
    # answer. Build one authoritative prompt from scratch instead.
    context_json = json.dumps(context, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"""你是 WY Wallet 的私人财务账本分析助手。

你现在拥有用户所选年份的完整账本上下文。下面数据由应用直接从 Supabase 读取，并由 Pandas 在本地生成精确聚合。

数据结构：
- transactions：逐笔交易，字段为 date / item / category / type / amount / note。
- monthly_item_expense：每个月每个项目的支出总额和笔数。
- monthly_category_expense：每个月每个类别的支出总额和笔数。
- daily_item_expense：每天每个项目的支出总额和笔数。
- monthly_totals：每个月总收入、总支出和交易笔数。
- ledger_complete=true 表示该年份全部交易已经包含在 transactions 中。

回答规则：
1. 只根据下面真实账本数据回答，不编造交易。
2. 金额问题优先读取本地聚合字段，不要凭感觉计算。
3. 用户用词和项目名称可以不同，要做合理语义匹配。例如“油费 / 打油 / 加油 / petrol / fuel / 汽油”可视为相近概念；“Grab / 打车 / e-hailing”也可做合理匹配。
4. 指定月份时，只使用该 month；指定日期时，只使用该 date。
5. 回答金额时说明实际纳入了哪些账本项目、共多少笔，并给出 RM 两位小数。
6. 如果 ledger_complete=true，不允许回答“资料只提供年度合计”“没有月份细分”“资料未提供明细”。你已经拥有完整逐笔交易。
7. 如果查遍完整账本后真的没有匹配，才回答“该范围内没有找到相关交易”。
8. 使用中文，回答简洁直接。

所选年份：{selected_year}
当前问题：{question}
近期对话（仅供理解上下文，不得覆盖账本事实）：
{history or '无'}

完整账本数据：
{context_json}
"""


_original_generative_model = getattr(genai.GenerativeModel, "_wy_original", genai.GenerativeModel)


class _FinanceAwareGenerativeModel:
    def __init__(self, *args, **kwargs):
        self._delegate = _original_generative_model(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def generate_content(self, contents, *args, **kwargs):
        return self._delegate.generate_content(_enrich_finance_prompt(contents), *args, **kwargs)


_FinanceAwareGenerativeModel._wy_original = _original_generative_model


_original_dataframe = getattr(st.dataframe, "_wy_original", st.dataframe)
_original_plotly_chart = getattr(st.plotly_chart, "_wy_original", st.plotly_chart)
_original_file_uploader = getattr(st.file_uploader, "_wy_original", st.file_uploader)
_original_tabs = getattr(st.tabs, "_wy_original", st.tabs)
_original_warning = getattr(st.warning, "_wy_original", st.warning)
_original_markdown = getattr(st.markdown, "_wy_original", st.markdown)
_original_caption = getattr(st.caption, "_wy_original", st.caption)
_chart_css_injected = False


def _stable_dataframe(*args, **kwargs):
    if kwargs.get("key") == "transaction_table":
        kwargs["selection_mode"] = ["single-row", "single-column"]
    return _original_dataframe(*args, **kwargs)


def _polish_figure(fig):
    if not hasattr(fig, "update_layout") or not hasattr(fig, "data"):
        return fig

    trace_types = {getattr(trace, "type", "") for trace in fig.data}
    horizontal_bar = any(
        getattr(trace, "type", "") == "bar" and getattr(trace, "orientation", None) == "h"
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
            bgcolor="rgba(0,0,0,0)", borderwidth=0, font=dict(size=11),
            itemclick=False, itemdoubleclick=False,
        ),
        transition=dict(duration=180, easing="cubic-in-out"),
    )
    if pie_only:
        fig.update_layout(hovermode="closest")
    elif getattr(fig.layout, "hovermode", None) in (None, False):
        fig.update_layout(hovermode="x unified")

    try:
        fig.update_xaxes(
            fixedrange=True, automargin=True, showgrid=horizontal_bar,
            gridcolor="rgba(148,163,184,.13)", griddash="dot", zeroline=False,
            tickfont=dict(size=11), title_font=dict(size=12),
        )
        fig.update_yaxes(
            fixedrange=True, automargin=True, showgrid=not horizontal_bar,
            gridcolor="rgba(148,163,184,.13)", griddash="dot", zeroline=False,
            tickfont=dict(size=11), title_font=dict(size=12),
        )
    except Exception:
        pass

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
        fig.update_traces(marker_line_width=0, opacity=.94, cliponaxis=False, selector=dict(type="bar"))
        fig.update_traces(marker_cornerradius=7, selector=dict(type="bar"))
    except Exception:
        pass
    try:
        fig.update_traces(line=dict(width=3), marker=dict(size=7), selector=dict(type="scatter"))
    except Exception:
        pass
    try:
        fig.update_traces(
            marker=dict(line=dict(color="rgba(255,255,255,.18)", width=1.5)),
            textfont=dict(size=12), pull=0, selector=dict(type="pie"),
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
        if not _chart_css_injected and "<style>" in body:
            body = body + CHART_CSS
            _chart_css_injected = True
    return _original_markdown(body, *args, **kwargs)


def _clean_caption(body, *args, **kwargs):
    if isinstance(body, str):
        body = body.replace(
            "只发送年度汇总、类别统计与最高金额记录，不发送完整账本。",
            "AI 会读取所选年份的完整交易明细，并结合本地精确聚合回答金额、月份、项目和类别问题。",
        )
        body = body.replace(
            "金额先由本地账本检索和聚合；AI 只接收与当前问题相关的汇总／明细，不发送整本账本。",
            "AI 会读取所选年份的完整交易明细，并结合本地精确聚合回答金额、月份、项目和类别问题。",
        )
    return _original_caption(body, *args, **kwargs)


_stable_dataframe._wy_original = _original_dataframe
_fixed_plotly_chart._wy_original = _original_plotly_chart
_without_import_uploader._wy_original = _original_file_uploader
_backup_only_tabs._wy_original = _original_tabs
_without_import_warning._wy_original = _original_warning
_clean_settings_copy._wy_original = _original_markdown
_clean_caption._wy_original = _original_caption

supabase_module.create_client = _paginated_create_client
genai.GenerativeModel = _FinanceAwareGenerativeModel
st.dataframe = _stable_dataframe
st.plotly_chart = _fixed_plotly_chart
st.file_uploader = _without_import_uploader
st.tabs = _backup_only_tabs
st.warning = _without_import_warning
st.markdown = _clean_settings_copy
st.caption = _clean_caption

# Clear old chat once because earlier answers were generated from incomplete
# summaries and can anchor the model to the wrong conclusion.
if not st.session_state.get("_wy_ai_full_ledger_v4_ready"):
    st.session_state.pop("ai_chat_history", None)
    st.session_state["_wy_ai_full_ledger_v4_ready"] = True

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
    st.caption = _original_caption
