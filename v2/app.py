"""Stable Streamlit entry point for WY Wallet V2.

The implementation lives in app_rich.py. runpy executes it on every Streamlit
rerun, avoiding normal Python module caching after buttons or filters change.

For the transaction ledger, column selection is enabled together with row
selection. Streamlit disables header sorting when column selection is enabled,
so the visible order is controlled only by the explicit Sort selector above the
ledger instead of being silently changed by an accidental header click.
"""

import runpy
from pathlib import Path

import streamlit as st


_original_dataframe = st.dataframe


def _stable_dataframe(*args, **kwargs):
    """Keep the transaction ledger ordered by the app's explicit sort control."""
    if kwargs.get("key") == "transaction_table":
        kwargs["selection_mode"] = ["single-row", "single-column"]
    return _original_dataframe(*args, **kwargs)


st.dataframe = _stable_dataframe
runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
