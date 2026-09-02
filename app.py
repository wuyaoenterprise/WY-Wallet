from __future__ import annotations

import streamlit as st

# The legacy root application is intentionally retired. The supported V3
# deployment entrypoint is v3/app.py. Keeping this file as a fail-closed guard
# prevents an accidentally reconfigured Streamlit deployment from silently
# starting the historical Smart Asset Pro runtime and its direct database reads.
ROOT_ENTRYPOINT_RETIRED = True
V3_ENTRYPOINT = "v3/app.py"

st.set_page_config(page_title="WY Wallet V3 · Retired entrypoint", page_icon="💳", layout="centered")
st.error("这个旧入口已经停用。")
st.info("请把 Streamlit 的 Main file path 设置为 `v3/app.py`。此页面不会读取或修改任何账本数据。")
st.code(V3_ENTRYPOINT)
st.stop()
