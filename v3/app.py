from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "v2"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

import wywallet.web as web

# V3 keeps only Streamlit fragment reruns at the deployment boundary. Ledger
# caching now has one source of truth in wywallet.db, avoiding the old
# per-session DataFrame cache that could show stale content for several minutes.
for _page_name in ("_transactions_page", "_reports_page", "_ai_page", "_settings_page"):
    _page = getattr(web, _page_name, None)
    if _page is not None and not getattr(_page, "_wy_v3_fragment", False):
        _fragment = st.fragment(_page)
        setattr(_fragment, "_wy_v3_fragment", True)
        setattr(web, _page_name, _fragment)

if __name__ == "__main__":
    web.run()
