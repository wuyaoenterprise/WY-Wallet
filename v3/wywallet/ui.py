from __future__ import annotations

import hashlib
import html

import plotly.graph_objects as go
import streamlit as st

from .config import CURRENCY_SYMBOL

CHART_COLORS = [
    "#5B8FF9", "#61DDAA", "#F6BD16", "#7262FD", "#78D3F8",
    "#9661BC", "#F6903D", "#E8684A", "#6DC8EC", "#9270CA",
]
SEMANTIC_COLORS = {
    "收入": "#35B77E", "Income": "#35B77E",
    "支出": "#EF6464", "Expense": "#EF6464",
    "退款": "#36A2AE", "Refund": "#36A2AE",
    "结余": "#F6BD16", "Balance": "#F6BD16",
}
LOCKED_CHART_CONFIG = {
    "displayModeBar": False, "displaylogo": False, "scrollZoom": False, "doubleClick": False,
    "showTips": False, "editable": False, "responsive": True,
}

CSS = """
<style>
:root{--wy-primary:#5b8ff9;--wy-positive:#218a63;--wy-negative:#d84f4f;--wy-refund:#248b96;--wy-warning:#b77900;--wy-border:rgba(128,128,128,.24);--wy-muted:color-mix(in srgb,var(--text-color,#667085) 68%,transparent)}
[data-testid="stAppViewContainer"]>.main .block-container{max-width:1280px;padding-top:1.15rem;padding-bottom:3rem}
[data-testid="stSidebar"]{border-right:1px solid var(--wy-border)}
.wy-brand{padding:.35rem 0 1rem}.wy-brand-title{font-size:1.45rem;font-weight:800;line-height:1.2}.wy-muted,.wy-brand-subtitle{color:var(--wy-muted);font-size:.88rem}
.wy-page-title{font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin:0 0 .15rem}.wy-page-subtitle{color:var(--wy-muted);margin-bottom:1.15rem}.wy-section-title{font-size:1.05rem;font-weight:760;margin:.2rem 0 .65rem}
.wy-card,.wy-detail{border:1px solid var(--wy-border);border-radius:14px;padding:1rem;background:rgba(127,127,127,.025)}.wy-detail{margin-top:.7rem}.wy-chip{display:inline-block;border:1px solid var(--wy-border);border-radius:999px;padding:.12rem .5rem;font-size:.78rem;color:var(--wy-muted)}
.wy-empty{border:1px dashed var(--wy-border);border-radius:14px;padding:2rem;text-align:center;color:var(--wy-muted)}.wy-amount-expense{color:var(--wy-negative);font-weight:800}.wy-amount-income{color:var(--wy-positive);font-weight:800}.wy-amount-refund{color:var(--wy-refund);font-weight:800}
div[data-testid="stMetric"]{border:1px solid var(--wy-border);border-radius:14px;padding:.82rem 1rem;background:rgba(127,127,127,.035);min-height:118px;box-sizing:border-box}div[data-testid="stMetricLabel"]{color:var(--wy-muted)}div[data-testid="stMetricValue"]{font-size:1.42rem}
.wy-calendar{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}.wy-calendar-head{text-align:center;color:var(--wy-muted);font-size:.78rem;padding:.25rem}.wy-calendar-day{min-height:68px;border:1px solid var(--wy-border);border-radius:10px;padding:.45rem}.wy-calendar-date{color:var(--wy-muted);font-size:.78rem}.wy-calendar-amount{font-size:.84rem;font-weight:750;margin-top:.42rem}
.wy-callout{border-left:3px solid var(--wy-primary);padding:.7rem .9rem;background:rgba(91,143,249,.08);border-radius:0 10px 10px 0;margin:.4rem 0}
[data-testid="stPlotlyChart"]{border:1px solid var(--wy-border);border-radius:16px;padding:.35rem .45rem .1rem;background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012));overflow:hidden}[data-testid="stPlotlyChart"] [data-testid="stElementToolbar"]{display:none!important}[data-testid="stPlotlyChart"] .js-plotly-plot,[data-testid="stPlotlyChart"] .plot-container{touch-action:pan-y!important}
@media(max-width:760px){[data-testid="stAppViewContainer"]>.main .block-container{padding-left:.7rem;padding-right:.7rem}.wy-page-title{font-size:1.55rem}.wy-calendar{gap:3px}.wy-calendar-day{min-height:48px;padding:.25rem}.wy-calendar-amount{font-size:.66rem;margin-top:.2rem}[data-testid="stPlotlyChart"]{border-radius:12px;padding:.15rem .1rem 0}div[data-testid="stMetric"]{padding:.65rem .75rem;min-height:104px}div[data-testid="stMetricValue"]{font-size:1.15rem}}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def money(value: float | int | None, signed: bool = False) -> str:
    number = float(value or 0)
    if signed:
        return f"{'+' if number >= 0 else '−'}{CURRENCY_SYMBOL} {abs(number):,.2f}"
    if number < 0:
        return f"−{CURRENCY_SYMBOL} {abs(number):,.2f}"
    return f"{CURRENCY_SYMBOL} {number:,.2f}"


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="wy-page-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="wy-page-subtitle">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def section_title(title: str) -> None:
    st.markdown(f'<div class="wy-section-title">{html.escape(title)}</div>', unsafe_allow_html=True)


def empty_state(text: str) -> None:
    st.markdown(f'<div class="wy-empty">{html.escape(text)}</div>', unsafe_allow_html=True)


def safe_detail_html(item: str, category: str, type_label: str, amount: float, tx_date: str, note: str, positive: bool) -> str:
    item_e, category_e, type_e, date_e, note_e = map(lambda x: html.escape(str(x)), [item, category, type_label, tx_date, note or ""])
    if str(type_label) == "退款":
        amount_class = "wy-amount-refund"
        sign = "+"
    else:
        amount_class = "wy-amount-income" if positive else "wy-amount-expense"
        sign = "+" if positive else "−"
    note_suffix = f" · {note_e}" if note_e else ""
    return (
        f'<div class="wy-detail"><span class="wy-chip">{type_e}</span> <span class="wy-chip">{category_e}</span>'
        f'<h3 style="margin:.55rem 0 .2rem">{item_e}</h3><div class="{amount_class}" style="font-size:1.35rem">{sign}{money(amount)}</div>'
        f'<div class="wy-muted" style="margin-top:.35rem">{date_e}{note_suffix}</div></div>'
    )


def stable_color(name: str) -> str:
    if name in SEMANTIC_COLORS:
        return SEMANTIC_COLORS[name]
    digest = hashlib.sha256(str(name).casefold().encode("utf-8")).digest()
    return CHART_COLORS[digest[0] % len(CHART_COLORS)]


def polish_figure(fig: go.Figure) -> go.Figure:
    if not hasattr(fig, "update_layout"):
        return fig
    trace_types = {getattr(trace, "type", "") for trace in fig.data}
    horizontal_bar = any(getattr(trace, "type", "") == "bar" and getattr(trace, "orientation", None) == "h" for trace in fig.data)
    pie_only = bool(trace_types) and trace_types.issubset({"pie"})
    fig.update_layout(
        dragmode=False, clickmode="none", uirevision="wy-wallet-fixed-chart",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=18, r=18, t=34, b=18), font=dict(size=13),
        hoverlabel=dict(bgcolor="rgba(20,24,33,.96)", bordercolor="rgba(255,255,255,.16)", font=dict(color="#FFFFFF", size=12)),
        legend=dict(bgcolor="rgba(0,0,0,0)", borderwidth=0, font=dict(size=11), itemclick=False, itemdoubleclick=False),
    )
    if pie_only:
        fig.update_layout(hovermode="closest")
    elif getattr(fig.layout, "hovermode", None) in (None, False):
        fig.update_layout(hovermode="x unified")
    try:
        fig.update_xaxes(fixedrange=True, automargin=True, showgrid=horizontal_bar, gridcolor="rgba(148,163,184,.13)", griddash="dot", zeroline=False, tickfont=dict(size=11), title_font=dict(size=12))
        fig.update_yaxes(fixedrange=True, automargin=True, showgrid=not horizontal_bar, gridcolor="rgba(148,163,184,.13)", griddash="dot", zeroline=False, tickfont=dict(size=11), title_font=dict(size=12))
    except Exception:
        pass
    for trace in fig.data:
        try:
            if getattr(trace, "type", "") == "pie":
                labels = list(getattr(trace, "labels", []) or [])
                trace.update(marker=dict(colors=[stable_color(str(label)) for label in labels], line=dict(color="rgba(255,255,255,.18)", width=1.5)), textfont=dict(size=12), pull=0)
                continue
            name = str(getattr(trace, "name", "") or "")
            color = stable_color(name) if name else CHART_COLORS[0]
            if getattr(trace, "type", "") == "scatter":
                trace.update(line=dict(color=color, width=3), marker=dict(color=color, size=7))
            elif getattr(trace, "type", "") == "bar":
                trace.update(marker_color=color)
        except Exception:
            pass
    try:
        fig.update_traces(marker_line_width=0, opacity=.94, cliponaxis=False, selector=dict(type="bar"))
        fig.update_traces(marker_cornerradius=7, selector=dict(type="bar"))
        fig.update_traces(line=dict(width=3), marker=dict(size=7), selector=dict(type="scatter"))
    except Exception:
        pass
    return fig


def render_chart(fig: go.Figure, *, height: int | None = None, legend: bool | None = None, hovermode: str | None = None) -> None:
    updates = {}
    if height is not None:
        updates["height"] = height
    if legend is not None:
        updates["showlegend"] = legend
    if hovermode is not None:
        updates["hovermode"] = hovermode
    if updates:
        fig.update_layout(**updates)
    st.plotly_chart(polish_figure(fig), width="stretch", config=LOCKED_CHART_CONFIG)
