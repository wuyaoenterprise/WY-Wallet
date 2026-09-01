from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "v2"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import wywallet.web as web


_original_inject_css = web.inject_css
_original_load_transactions = web.load_transactions
_original_load_invalid_transactions = web.load_invalid_transactions


def _inject_v3_css() -> None:
    """Apply V3 branding during the normal page-render phase."""
    _original_inject_css()
    st.markdown(
        """
        <style>
        .wy-brand-subtitle {
            font-size: 0 !important;
        }
        .wy-brand-subtitle::after {
            content: "个人财务中心 · V3";
            font-size: 0.82rem !important;
            color: inherit;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


web.inject_css = _inject_v3_css


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def _cached_valid_transactions(data_revision: int):
    # data_revision changes on every V3 write or explicit Refresh action.
    return _original_load_transactions()


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def _cached_invalid_transactions(data_revision: int):
    return _original_load_invalid_transactions()


def _fast_load_transactions():
    return _cached_valid_transactions(int(st.session_state.get("data_revision", 0)))


def _fast_load_invalid_transactions():
    return _cached_invalid_transactions(int(st.session_state.get("data_revision", 0)))


web.load_transactions = _fast_load_transactions
web.load_invalid_transactions = _fast_load_invalid_transactions


# Streamlit normally reruns the entire script for every widget interaction.
# These page bodies are independent enough to use fragment reruns: filters,
# sort controls, report sections, settings sections and AI-page controls can
# update without rebuilding the sidebar and the rest of the application.
# Data-changing dialogs still call st.rerun() (app scope), so writes continue
# to invalidate caches and refresh all dependent views immediately.
for _page_name in ("_transactions_page", "_reports_page", "_ai_page", "_settings_page"):
    _page = getattr(web, _page_name, None)
    if _page is not None and not getattr(_page, "_wy_v3_fragment", False):
        _fragment = st.fragment(_page)
        setattr(_fragment, "_wy_v3_fragment", True)
        setattr(web, _page_name, _fragment)


if __name__ == "__main__":
    web.run()
