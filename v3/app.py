from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SHARED_WEB_CORE = ROOT / "v2"
V3_ROOT = ROOT / "v3"
for path in (SHARED_WEB_CORE, V3_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import wywallet.web as web
from v3_overrides import apply_overrides, expire_access_session_if_needed, render_session_controls

apply_overrides()
expire_access_session_if_needed()

# Page-local interactions use fragment reruns for speed, but each fragment now
# reloads the shared ledger snapshot inside the fragment instead of holding the
# parent run's DataFrame arguments. That keeps cross-session V3 writes visible
# on the next interaction while retaining fragment performance.

def _fragment_transactions(_transactions, _categories):
    transactions = web.load_transactions()
    categories = web._sorted_categories(transactions)
    return _ORIGINAL_PAGES["_transactions_page"](transactions, categories)


def _fragment_reports(_transactions, _invalid_rows):
    return _ORIGINAL_PAGES["_reports_page"](web.load_transactions(), web.load_invalid_transactions())


def _fragment_ai(_transactions):
    return _ORIGINAL_PAGES["_ai_page"](web.load_transactions())


def _fragment_settings(_transactions, _invalid_rows, _categories):
    transactions = web.load_transactions()
    return _ORIGINAL_PAGES["_settings_page"](
        transactions,
        web.load_invalid_transactions(),
        web._sorted_categories(transactions),
    )


_ORIGINAL_PAGES = {
    "_transactions_page": web._transactions_page,
    "_reports_page": web._reports_page,
    "_ai_page": web._ai_page,
    "_settings_page": web._settings_page,
}
_FRAGMENT_FACTORIES = {
    "_transactions_page": _fragment_transactions,
    "_reports_page": _fragment_reports,
    "_ai_page": _fragment_ai,
    "_settings_page": _fragment_settings,
}
for _name, _factory in _FRAGMENT_FACTORIES.items():
    _fragment = st.fragment(_factory)
    setattr(_fragment, "_wy_v3_fragment", True)
    setattr(web, _name, _fragment)

if __name__ == "__main__":
    web.run()
    render_session_controls()
