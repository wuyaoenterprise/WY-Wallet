"""Stable Streamlit entry point for WY Wallet V2.

The full interface remains in ``app_rich.py``. This entry point applies a few
small compatibility adapters before executing it:

1. Transaction rows are fetched from Supabase in pages so datasets larger than
   the server's per-request row limit are displayed completely.
2. The transaction ledger order is controlled by the app's explicit sort menu,
   not accidental clicks on table headers.
3. Import interfaces have been removed; export backup remains available.
"""

from pathlib import Path
from types import SimpleNamespace
import runpy

import streamlit as st
import supabase as supabase_module


BATCH_SIZE = 1000
MAX_TRANSACTION_ROWS = 100_000


class _QueryProxy:
    """Record a Supabase query chain and replay it with pagination when needed."""

    def __init__(self, client, table_name: str, calls=None, operation: str | None = None):
        self._client = client
        self._table_name = table_name
        self._calls = list(calls or [])
        self._operation = operation

    def _append(self, name, args, kwargs, operation=None):
        return _QueryProxy(
            self._client,
            self._table_name,
            self._calls + [(name, args, kwargs)],
            operation or self._operation,
        )

    def select(self, *args, **kwargs):
        return self._append("select", args, kwargs, "select")

    def insert(self, *args, **kwargs):
        return self._append("insert", args, kwargs, "insert")

    def update(self, *args, **kwargs):
        return self._append("update", args, kwargs, "update")

    def delete(self, *args, **kwargs):
        return self._append("delete", args, kwargs, "delete")

    def upsert(self, *args, **kwargs):
        return self._append("upsert", args, kwargs, "upsert")

    def __getattr__(self, name):
        def chained(*args, **kwargs):
            return self._append(name, args, kwargs)

        return chained

    def _build(self, page_range=None):
        builder = self._client.table(self._table_name)
        for name, args, kwargs in self._calls:
            builder = getattr(builder, name)(*args, **kwargs)
        if page_range is not None:
            builder = builder.range(page_range[0], page_range[1])
        return builder

    def execute(self):
        # The app's main transaction query previously returned only the newest
        # server-limited page. Replay transaction SELECTs in 1,000-row pages.
        has_explicit_range = any(name == "range" for name, _, _ in self._calls)
        if self._table_name != "transactions" or self._operation != "select" or has_explicit_range:
            return self._build().execute()

        all_rows = []
        first_response = None
        offset = 0
        while offset < MAX_TRANSACTION_ROWS:
            response = self._build((offset, offset + BATCH_SIZE - 1)).execute()
            if first_response is None:
                first_response = response
            rows = list(getattr(response, "data", None) or [])
            all_rows.extend(rows)
            if len(rows) < BATCH_SIZE:
                break
            offset += BATCH_SIZE

        return SimpleNamespace(
            data=all_rows,
            count=getattr(first_response, "count", None),
        )


class _ClientProxy:
    def __init__(self, client):
        self._client = client

    def table(self, table_name: str):
        return _QueryProxy(self._client, table_name)

    def __getattr__(self, name):
        return getattr(self._client, name)


_original_create_client = getattr(
    supabase_module.create_client,
    "_wy_original",
    supabase_module.create_client,
)


def _paginated_create_client(*args, **kwargs):
    return _ClientProxy(_original_create_client(*args, **kwargs))


_paginated_create_client._wy_original = _original_create_client


# Keep references to the real Streamlit functions so wrappers do not stack on
# reruns when Streamlit reuses modules.
_original_dataframe = getattr(st.dataframe, "_wy_original", st.dataframe)
_original_file_uploader = getattr(st.file_uploader, "_wy_original", st.file_uploader)
_original_tabs = getattr(st.tabs, "_wy_original", st.tabs)
_original_warning = getattr(st.warning, "_wy_original", st.warning)
_original_markdown = getattr(st.markdown, "_wy_original", st.markdown)


def _stable_dataframe(*args, **kwargs):
    if kwargs.get("key") == "transaction_table":
        # Column selection disables built-in header sorting while row selection
        # still opens transaction details.
        kwargs["selection_mode"] = ["single-row", "single-column"]
    return _original_dataframe(*args, **kwargs)


def _without_import_uploader(label, *args, **kwargs):
    if kwargs.get("key") == "import_file":
        return None
    return _original_file_uploader(label, *args, **kwargs)


def _backup_only_tabs(labels, *args, **kwargs):
    if list(labels) == ["类别管理", "备份与导入"]:
        labels = ["类别管理", "备份"]
    return _original_tabs(labels, *args, **kwargs)


def _without_import_warning(body, *args, **kwargs):
    if isinstance(body, str) and body.startswith("导入只会新增"):
        return None
    return _original_warning(body, *args, **kwargs)


def _clean_settings_copy(body, *args, **kwargs):
    if isinstance(body, str):
        body = body.replace(
            "管理类别、导出备份，以及安全导入历史数据。",
            "管理类别并导出备份。",
        )
    return _original_markdown(body, *args, **kwargs)


_stable_dataframe._wy_original = _original_dataframe
_without_import_uploader._wy_original = _original_file_uploader
_backup_only_tabs._wy_original = _original_tabs
_without_import_warning._wy_original = _original_warning
_clean_settings_copy._wy_original = _original_markdown

supabase_module.create_client = _paginated_create_client
st.dataframe = _stable_dataframe
st.file_uploader = _without_import_uploader
st.tabs = _backup_only_tabs
st.warning = _without_import_warning
st.markdown = _clean_settings_copy

try:
    runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
finally:
    supabase_module.create_client = _original_create_client
    st.dataframe = _original_dataframe
    st.file_uploader = _original_file_uploader
    st.tabs = _original_tabs
    st.warning = _original_warning
    st.markdown = _original_markdown
