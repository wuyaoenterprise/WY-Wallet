"""Stable Streamlit entry point for WY Wallet V2.

The implementation lives in app_rich.py. This entry point adds two small,
non-destructive UI adapters before executing the main application:

1. The transaction ledger keeps its order controlled by the explicit sort
   selector instead of accidental header sorting.
2. The old strict backup uploader becomes a guided import chooser, so messy
   files are routed to the AI or flexible mapping import pages instead of being
   rejected as if they were WY Wallet backup files.
"""

from pathlib import Path
import runpy

import streamlit as st


# Keep references to the real Streamlit functions. The marker prevents wrapper
# stacking if Streamlit reuses the imported module across reruns.
_original_dataframe = getattr(st.dataframe, "_wy_original", st.dataframe)
_original_file_uploader = getattr(st.file_uploader, "_wy_original", st.file_uploader)


def _stable_dataframe(*args, **kwargs):
    """Keep the transaction ledger ordered by the app's explicit sort control."""
    if kwargs.get("key") == "transaction_table":
        # Enabling column selection disables Streamlit's header-click sorting,
        # while single-row selection still opens transaction details.
        kwargs["selection_mode"] = ["single-row", "single-column"]
    return _original_dataframe(*args, **kwargs)


def _guided_file_uploader(label, *args, **kwargs):
    """Replace only the legacy strict importer with a clear import router."""
    if kwargs.get("key") != "import_file":
        return _original_file_uploader(label, *args, **kwargs)

    st.markdown('<div class="wy-section-title">选择导入方式</div>', unsafe_allow_html=True)
    mode = st.radio(
        "导入方式",
        ["AI 智能整理", "通用栏位映射", "WY Wallet 标准备份"],
        horizontal=True,
        label_visibility="collapsed",
        key="settings_import_mode",
    )

    if mode == "AI 智能整理":
        st.info(
            "适合栏位混乱、多工作表、PDF、截图、Word 或格式不统一的资料。"
            "Gemini 会先整理成候选交易，必须由你检查并确认后才会写入数据库。"
        )
        st.page_link(
            "pages/6_AI智能整理导入.py",
            label="打开 AI 智能整理导入",
            icon="✨",
            use_container_width=True,
        )
        return None

    if mode == "通用栏位映射":
        st.info(
            "适合 CSV／Excel 的资料结构基本整齐，但日期、金额、Debit、Credit、商家等栏位名称与 WY Wallet 不同。"
        )
        st.page_link(
            "pages/5_数据导入.py",
            label="打开通用数据导入向导",
            icon="📥",
            use_container_width=True,
        )
        return None

    st.caption(
        "这里只接受由 WY Wallet 导出的标准备份，必须包含 date、item、category、type、amount；note 可选。"
    )
    return _original_file_uploader(
        "上传 WY Wallet 标准 CSV 或 Excel 备份",
        *args,
        **kwargs,
    )


_stable_dataframe._wy_original = _original_dataframe
_guided_file_uploader._wy_original = _original_file_uploader
st.dataframe = _stable_dataframe
st.file_uploader = _guided_file_uploader

try:
    runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")

    # Keep the two import tools discoverable even when the user is not on the
    # settings page. They remain separate pages so a failed import cannot break
    # the main wallet interface.
    with st.sidebar:
        st.divider()
        st.caption("数据导入工具")
        st.page_link("pages/6_AI智能整理导入.py", label="AI 智能整理导入", icon="✨")
        st.page_link("pages/5_数据导入.py", label="通用栏位映射", icon="📥")
finally:
    # Avoid accumulating wrappers across Streamlit reruns.
    st.dataframe = _original_dataframe
    st.file_uploader = _original_file_uploader
