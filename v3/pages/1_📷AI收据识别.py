from __future__ import annotations

import streamlit as st

# Compatibility route for existing transaction-page links and old bookmarks.
# The destination performs its own access gate before any ledger read.
st.switch_page("pages/receipt.py")
