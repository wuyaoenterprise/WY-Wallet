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


def _inject_v3_css() -> None:
    """Apply V3 branding during the normal page-render phase.

    The shared stabilized core still contains one legacy V2 subtitle string.
    Injecting the override from web.inject_css guarantees the style is present
    before the sidebar is rendered on every Streamlit rerun.
    """
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


if __name__ == "__main__":
    web.run()
