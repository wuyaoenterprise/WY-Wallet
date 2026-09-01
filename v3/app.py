from __future__ import annotations

import hmac
import sys
import time
from pathlib import Path

import streamlit as st

V3_ROOT = Path(__file__).resolve().parent
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

import wywallet.web as web
from wywallet.config import ACCESS_SESSION_TTL_SECONDS, APP_TITLE, APP_VERSION, BUILD_ID, TIMEZONE_NAME
from wywallet.db import data_loaded_at, load_invalid_transactions, load_transactions, refresh_data, transactions_truncated
from wywallet.ui import inject_css, page_header

st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
inject_css()


def _truthy_secret(name: str) -> bool:
    try:
        return str(st.secrets.get(name, "") or "").strip().casefold() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def _configured_password() -> str:
    try:
        return str(st.secrets.get("WEB_ACCESS_PASSWORD", "") or "")
    except Exception:
        return ""


def _access_gate() -> str:
    configured = _configured_password()
    if not configured:
        if _truthy_secret("ALLOW_UNPROTECTED_ACCESS"):
            return "platform-private"
        page_header(
            "安全设置未完成",
            "请在 Streamlit Secrets 配置 WEB_ACCESS_PASSWORD；只有 App 已由 Streamlit 平台设为 Private 时，才建议使用 ALLOW_UNPROTECTED_ACCESS = true。",
        )
        st.code('WEB_ACCESS_PASSWORD = "请设置自己的强密码"', language="toml")
        st.stop()

    now = time.time()
    if st.session_state.get("web_access_ok"):
        last = float(st.session_state.get("web_access_ok_at", now) or now)
        if now - last <= ACCESS_SESSION_TTL_SECONDS:
            st.session_state["web_access_ok_at"] = now
            return "password"
        st.session_state.pop("web_access_ok", None)
        st.session_state.pop("web_access_ok_at", None)
        st.info("访问会话已过期，请重新输入密码。")

    lock_until = float(st.session_state.get("web_access_lock_until", 0) or 0)
    remaining = int(max(lock_until - now, 0))
    page_header("WY Wallet 私人访问", "请输入 WEB_ACCESS_PASSWORD。闲置 30 分钟后会自动锁定。")
    if remaining > 0:
        st.error(f"连续错误次数过多，请 {remaining} 秒后再试。")
        st.stop()

    entered = st.text_input("访问密码", type="password", key="v3_access_password")
    if st.button("进入", type="primary", width="stretch", key="v3_access_submit"):
        if hmac.compare_digest(entered, configured):
            st.session_state["web_access_ok"] = True
            st.session_state["web_access_ok_at"] = now
            st.session_state.pop("web_access_fail_count", None)
            st.session_state.pop("web_access_lock_until", None)
            st.rerun()
        count = int(st.session_state.get("web_access_fail_count", 0)) + 1
        if count >= 5:
            st.session_state["web_access_fail_count"] = 0
            st.session_state["web_access_lock_until"] = now + 30
            st.error("密码连续错误 5 次，已暂时锁定 30 秒。")
        else:
            st.session_state["web_access_fail_count"] = count
            st.error(f"密码不正确。再错 {5 - count} 次会进入短暂冷却。")
    st.stop()
    return "password"


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
    access_mode = _access_gate()

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
        st.info("请检查 Streamlit Secrets 中的 SUPABASE_URL / SUPABASE_KEY，或稍后按右上角菜单 Reboot app。")
        st.stop()
    loading.empty()

    if not invalid_rows.empty:
        st.warning(f"数据库有 {len(invalid_rows)} 笔无效记录，已从统计排除。请到「设置与备份 → 数据修复」处理。")

    with st.sidebar:
        st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V3</div></div>', unsafe_allow_html=True)
        if st.button("＋ 新增交易", type="primary", width="stretch"):
            web.add_transaction_dialog(categories)
        st.page_link("pages/1_📷AI收据识别.py", label="📷 AI 收据识别", width="stretch")
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
        web.render_session_controls()
        st.caption(f"数据读取：{data_loaded_at() or '未知'}")
        st.caption(f"{APP_VERSION} · {BUILD_ID}")
        st.caption(f"Malaysia time · {TIMEZONE_NAME}")
        st.caption("🔒 密码保护已启用" if access_mode == "password" else "🔒 由平台私有访问保护")

    if truncated and navigation in {"总览", "分析报表", "AI 洞察"}:
        page_header("数据量超过互动统计上限", "为避免把部分数据误当完整账本，本页已停止计算。")
        st.error("交易已超过 100,000 笔。请先到「设置与备份」制作完整备份并归档，或调整数据库方案后再继续统计。")
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
