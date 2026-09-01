from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, EXPENSE, INCOME, today_my
from wywallet.db import create_category, existing_transaction_keys, insert_transactions, load_categories, load_transactions, normalize_transaction, transaction_key
from wywallet.ui import inject_css, money, page_header

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()
page_header("📷 AI 收据识别", "上传或拍摄一张收据；Gemini 3.7 Flash 只负责提取，保存前由你核对，金额与重复记录会再次验证。")
st.page_link("app.py", label="← 返回 WY Wallet", use_container_width=False)

transactions = load_transactions()
categories = load_categories(transactions)

upload_tab, camera_tab = st.tabs(["上传图片", "直接拍照"])
with upload_tab:
    uploaded = st.file_uploader("上传 JPG、PNG 或 WebP", type=["jpg", "jpeg", "png", "webp"], key="receipt_upload_v3")
with camera_tab:
    captured = st.camera_input("拍摄收据", key="receipt_camera_v3")

source = captured or uploaded
if source is None:
    st.info("选择图片或拍照后即可识别。")
    st.stop()

raw = source.getvalue()
signature = hashlib.sha256(raw).hexdigest()[:20]
if st.session_state.get("receipt_signature") != signature:
    st.session_state["receipt_signature"] = signature
    st.session_state.pop("receipt_result", None)

preview, action = st.columns([1, 1.25], gap="large")
with preview:
    st.image(raw, caption="待识别收据", use_container_width=True)
with action:
    instruction = st.text_area("补充说明（可选）", placeholder="例如：这是退款单；日期实际是昨天。")
    if st.button("✨ 使用 Gemini 3.7 Flash 识别", type="primary", use_container_width=True):
        try:
            mime = getattr(source, "type", None) or "image/jpeg"
            with st.spinner("正在读取收据并拆分项目…"):
                result = recognize_receipt(raw, mime, categories, instruction.strip())
            st.session_state["receipt_result"] = result.model_dump()
            st.rerun()
        except Exception as exc:
            st.error(f"收据识别失败：{exc}")
    if st.button("清除识别结果", use_container_width=True):
        st.session_state.pop("receipt_result", None); st.rerun()

payload = st.session_state.get("receipt_result")
if not payload:
    st.stop()

for warning in payload.get("warnings") or []:
    st.warning(str(warning))
rows = payload.get("transactions") or []
if not rows:
    st.warning("AI 没有识别到可用交易，请换更清晰的图片。")
    st.stop()

frame = pd.DataFrame(rows)
for column, default in {"date": today_my(), "item": "", "category": "其他", "type": EXPENSE, "amount": None, "note": ""}.items():
    if column not in frame: frame[column] = default
frame["date"] = pd.to_datetime(frame["date"], errors="coerce").fillna(pd.Timestamp(today_my()))
frame["category"] = frame["category"].where(frame["category"].isin(categories), "其他")
frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, INCOME]), EXPENSE)
frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
frame.insert(0, "保存", True)

st.subheader("检查并修改")
st.caption("AI 结果不是最终账目。请核对日期、项目、类别和金额；保存时系统会基于你编辑后的最终内容再次检查重复。")
new_cat_col, create_col = st.columns([2, 1])
new_cat = new_cat_col.text_input("需要新类别时先建立", placeholder="例如：宠物")
if create_col.button("＋ 建立类别", use_container_width=True):
    try:
        created = create_category(new_cat)
        st.toast("类别已建立" if created else "类别已存在")
        st.rerun()
    except Exception as exc:
        st.error(f"建立类别失败：{exc}")

edited = st.data_editor(
    frame[["保存", "date", "item", "category", "type", "amount", "note"]],
    hide_index=True, use_container_width=True, num_rows="dynamic",
    column_config={
        "保存": st.column_config.CheckboxColumn("保存"),
        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
        "item": st.column_config.TextColumn("项目／商家", required=True, width="large"),
        "category": st.column_config.SelectboxColumn("类别", options=categories, required=True),
        "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, INCOME], required=True),
        "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
        "note": st.column_config.TextColumn("备注", width="large"),
    },
    key=f"receipt_editor_{signature}",
)

existing = existing_transaction_keys()
seen_candidate_keys = set()
statuses = []
normalized_rows = []
for _, row in edited.iterrows():
    if not bool(row.get("保存")):
        statuses.append("未选择"); normalized_rows.append(None); continue
    try:
        normalized = normalize_transaction(row.to_dict())
        key = transaction_key(normalized)
        if key in existing or key in seen_candidate_keys:
            statuses.append("疑似重复")
        else:
            statuses.append("可保存"); seen_candidate_keys.add(key)
        normalized_rows.append(normalized)
    except Exception as exc:
        statuses.append(f"无效：{exc}"); normalized_rows.append(None)

summary = edited.copy(); summary["状态"] = statuses
st.dataframe(summary[["保存", "date", "item", "category", "type", "amount", "状态"]], hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})

valid_rows = [row for row, status in zip(normalized_rows, statuses) if row is not None and status == "可保存"]
duplicate_count = sum(status == "疑似重复" for status in statuses)
expense_total = sum(row["amount"] for row in valid_rows if row["type"] == EXPENSE)
income_total = sum(row["amount"] for row in valid_rows if row["type"] == INCOME)
a, b, c, d = st.columns(4)
a.metric("准备保存", f"{len(valid_rows)} 笔"); b.metric("疑似重复", f"{duplicate_count} 笔"); c.metric("支出", money(expense_total)); d.metric("收入", money(income_total))
if payload.get("receipt_total") is not None:
    st.caption(f"AI 读取的收据总额：{money(payload['receipt_total'])}。请自行确认它与项目合计是否一致。")

confirm = st.checkbox(f"我已核对，并确认新增 {len(valid_rows)} 笔非重复交易。", disabled=not valid_rows)
if st.button("保存选中项目", type="primary", use_container_width=True, disabled=not confirm or not valid_rows):
    try:
        latest_existing = existing_transaction_keys(fresh=True)
        final_rows = [row for row in valid_rows if transaction_key(row) not in latest_existing]
        skipped = len(valid_rows) - len(final_rows)
        if not final_rows:
            st.warning("这些记录现在都已存在，没有新增。")
        else:
            saved = insert_transactions(final_rows)
            st.session_state.pop("receipt_result", None)
            st.toast(f"成功保存 {saved} 笔交易" + (f"，跳过 {skipped} 笔重复" if skipped else ""))
            st.balloons(); st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
