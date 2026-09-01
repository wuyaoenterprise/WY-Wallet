from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SHARED_WEB_CORE = ROOT / "v2"
if str(SHARED_WEB_CORE) not in sys.path:
    sys.path.insert(0, str(SHARED_WEB_CORE))

# V3 deliberately uses one tested web-core package rather than maintaining two
# copies of accounting/AI logic. The deployment boundary, branding, access
# policy, tests and versioning are V3; the historical `v2/` directory name is
# now only the physical location of that shared core on this branch.
import wywallet.web as web

# Page-local interactions use fragment reruns so filters/report switches do not
# rebuild the whole app. Ledger caching has a single source of truth in db.py.
for _page_name in ("_transactions_page", "_reports_page", "_ai_page", "_settings_page"):
    _page = getattr(web, _page_name, None)
    if _page is not None and not getattr(_page, "_wy_v3_fragment", False):
        _fragment = st.fragment(_page)
        setattr(_fragment, "_wy_v3_fragment", True)
        setattr(web, _page_name, _fragment)

if __name__ == "__main__":
    web.run()
