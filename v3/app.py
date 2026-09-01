from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

V3_ROOT = Path(__file__).resolve().parent
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

import wywallet.web as web
from wywallet.access import render_lock_button, require_access
from wywallet.config import APP_TITLE, APP_VERSION, BUILD_ID, TIMEZONE_NAME
from wywallet.db import data_loaded_at, load_invalid_transactions, load_transactions, refresh_data, transactions_truncated
from wywallet.ui import inject_css, page_header

st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
inject_css()


@st.fragment
def _transactions_fragment() -> None:
    tx = load_transactions()
    web._transactions_page(tx, web._sorted_categories(tx))


@st.fragment
def _reports_fragment() -> None:
    web._reports_page(load_transactions(), load_invalid_transactions())


@st.fragment
def _ai_fragment() -> None:
    web._ai_page(load_transactions())


@st.fragment
def _settings_fragment() -> None:
    tx = load_transactions()
    web._settings_page(tx, load_invalid_transactions(), web._sorted_categories(tx))


def main() -> None:
    access_mode = require_access()

    loading = st.empty()
    loading.info("正在连接财务数据库…")
    try:
        transactions = load_transactions()
        invalid_rows = load_invalid_transactions()
        categories = web._sorted_categories(transactions)
        truncated = transactions_truncated()
    except Exception as exc:
        loading.empty()
        page_header("无法连接财务数据库", "网站已经正常启动，但 Supabase 数据读取失败。")
        st.error(str(exc))
        st.info("请检查 Streamlit Secrets 中的 SUPABASE_URL / SUPABASE_KEY，或稍后使用右上角菜单 Reboot app。")
        st.stop()
    loading.empty()

    if not invalid_rows.empty:
        st.warning(f"数据库有 {len(invalid_rows)} 笔无效记录，已从统计排除。请到「设置与备份 → 数据修复」处理。")

    with st.sidebar:
        st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V3</div></div>', unsafe_allow_html=True)
        if st.button("＋ 新增交易", type="primary", width="stretch"):
            web.add_transaction_dialog(categories)
        st.page_link("pages/receipt.py", label="📷 AI 收据识别", width="stretch")
        navigation = st.radio(
            "导航",
            ["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"],
            format_func=lambda value: {
                "总览": "⌂  总览",
                "交易记录": "≡  交易记录",
                "分析报表": "▥  分析报表",
                "AI 洞察": "✦  AI 洞察",
                "设置与备份": "⚙  设置与备份",
            }[value],
            label_visibility="collapsed",
        )
        st.divider()
        if st.button("↻ 刷新数据", width="stretch"):
            refresh_data()
            st.rerun()
        render_lock_button()
        st.caption(f"数据读取：{data_loaded_at() or '未知'}")
        st.caption(f"{APP_VERSION} · {BUILD_ID}")
        st.caption(f"Malaysia time · {TIMEZONE_NAME}")
        st.caption("🔒 密码保护已启用" if access_mode == "password" else "🔒 由平台私有访问保护")

    if truncated and navigation in {"总览", "分析报表", "AI 洞察"}:
        page_header("数据量超过互动统计上限", "为避免把部分数据误当完整账本，本页已停止计算。")
        st.error("交易已超过 100,000 笔。请先到「设置与备份」制作完整备份并归档，或升级数据库查询方案后再继续统计。")
        return

    if navigation == "总览":
        web._dashboard(transactions)
    elif navigation == "交易记录":
        _transactions_fragment()
    elif navigation == "分析报表":
        _reports_fragment()
    elif navigation == "AI 洞察":
        _ai_fragment()
    else:
        _settings_fragment()


if __name__ == "__main__":
    main()
