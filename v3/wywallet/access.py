from __future__ import annotations

import hmac
import time

import streamlit as st

from .config import ACCESS_SESSION_TTL_SECONDS
from .ui import page_header


def truthy_secret(name: str) -> bool:
    try:
        return str(st.secrets.get(name, "") or "").strip().casefold() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def configured_password() -> str:
    try:
        return str(st.secrets.get("WEB_ACCESS_PASSWORD", "") or "")
    except Exception:
        return ""


def touch_access(*, stop_on_expired: bool = True) -> str:
    configured = configured_password()
    if not configured:
        if truthy_secret("ALLOW_UNPROTECTED_ACCESS"):
            return "platform-private"
        if stop_on_expired:
            st.error("安全设置未完成。")
            st.stop()
        return "unconfigured"

    now = time.time()
    if st.session_state.get("web_access_ok"):
        last = float(st.session_state.get("web_access_ok_at", now) or now)
        if now - last <= ACCESS_SESSION_TTL_SECONDS:
            st.session_state["web_access_ok_at"] = now
            return "password"
        st.session_state.pop("web_access_ok", None)
        st.session_state.pop("web_access_ok_at", None)

    if stop_on_expired:
        st.info("访问会话已过期，请重新输入密码。")
        st.stop()
    return "password-required"


def require_access() -> str:
    configured = configured_password()
    if not configured:
        if truthy_secret("ALLOW_UNPROTECTED_ACCESS"):
            return "platform-private"
        page_header(
            "安全设置未完成",
            "请在 Streamlit Secrets 配置 WEB_ACCESS_PASSWORD；只有 App 已由 Streamlit 平台设为 Private 时，才建议设置 ALLOW_UNPROTECTED_ACCESS = true。",
        )
        st.code('WEB_ACCESS_PASSWORD = "请设置自己的强密码"', language="toml")
        st.stop()

    mode = touch_access(stop_on_expired=False)
    if mode == "password":
        return mode

    now = time.time()
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
    return "password-required"


def render_lock_button() -> None:
    if not st.session_state.get("web_access_ok"):
        return
    if st.button("🔒 锁定此会话", width="stretch", key="v3_lock_session"):
        st.session_state.pop("web_access_ok", None)
        st.session_state.pop("web_access_ok_at", None)
        st.rerun()
