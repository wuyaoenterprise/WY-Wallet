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
"""

from pathlib import Path
from types import SimpleNamespace
import runpy

import streamlit as st
import supabase as supabase_module


BATCH_SIZE = 1000
MAX_TRANSACTION_ROWS = 100_000

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
st.dataframe = _stable_dataframe
st.plotly_chart = _fixed_plotly_chart
st.file_uploader = _without_import_uploader
st.tabs = _backup_only_tabs
st.warning = _without_import_warning
st.markdown = _clean_settings_copy

try:
    runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
finally:
    supabase_module.create_client = _original_create_client
    st.dataframe = _original_dataframe
    st.plotly_chart = _original_plotly_chart
    st.file_uploader = _original_file_uploader
    st.tabs = _original_tabs
    st.warning = _original_warning
    st.markdown = _original_markdown
