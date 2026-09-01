from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "v2"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from wywallet.web import run


if __name__ == "__main__":
    run()
    # The stabilized shared core still contains one legacy V2 subtitle string.
    # V3 owns its deployment branding, so override only the presentation here
    # without changing the frozen V2 deployment.
    st.markdown(
        """
        <style>
        .wy-brand-subtitle { font-size: 0 !important; }
        .wy-brand-subtitle::after {
            content: "个人财务中心 · V3";
            font-size: 0.82rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
