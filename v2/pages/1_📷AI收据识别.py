from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, EXPENSE, INCOME, today_my
from wywallet.db import create_category, existing_transaction_keys, insert_transactions, load_categories, load_transactions
from wywallet.receipt import evaluate_receipt_candidates, finalize_receipt_candidates, reconcile_receipt_total
from wywallet.ui import inject_css, money, page_header

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()
page_header("📷 AI 收据识别", "Gemini 3.7 Flash 负责提取；日期、重复项、项目合计和收据总额都会在保存前由本地逻辑重新验证。")
st.page_link("app.py", label="← 返回 WY Wallet", use_container_width=False)

try:
    transactions = load_transactions()
    categories = load_categories(transactions)
except Exception as exc:
    st.error(f"无法读取 Supabase：{exc}")
    st.stop()

upload_tab, camera_tab = st.tabs(["上传图片", "直接拍照"])
with upload_tab:
    uploaded = st.file_uploader("上传 JPG、PNG 或 WebP", type=["jpg", "jpeg", "png", "webp"], key="receipt_upload_v4")
with camera_tab:
    captured = st.camera_input("拍摄收据", key="receipt_camera_v4")
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
        st.session_state.pop("receipt_result", None)
        st.rerun()

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
for column, default in {"date": None, "item": "", "category": "其他", "type": EXPENSE, "amount": None, "note": ""}.items():
    if column not in frame:
        frame[column] = default
parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
date_missing = parsed_dates.isna()
frame["date"] = parsed_dates.fillna(pd.Timestamp(today_my()))
fallback_category = "其他" if "其他" in categories else (categories[0] if categories else "其他")
frame["category"] = frame["category"].where(frame["category"].isin(categories), fallback_category)
frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, INCOME]), EXPENSE)
frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
frame.insert(0, "仍然保存重复", False)
frame.insert(0, "日期已确认", (~date_missing).tolist())
frame.insert(0, "保存", True)

st.subheader("检查并修改")
st.caption("若 AI 看不清日期，系统会暂填今天但不会允许保存，直到你勾选「日期已确认」。疑似重复默认不保存，但真实重复交易可以显式勾选「仍然保存重复」。")
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
    frame[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "note"]],
    hide_index=True, use_container_width=True, num_rows="dynamic",
    column_config={
        "保存": st.column_config.CheckboxColumn("保存"),
        "日期已确认": st.column_config.CheckboxColumn("日期已确认", help="AI 无法读出日期时必须由你确认后才能保存"),
        "仍然保存重复": st.column_config.CheckboxColumn("仍然保存重复", help="只有确认两笔相同交易都真实存在时才勾选"),
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
statuses, candidates = evaluate_receipt_candidates(edited, existing)
summary = edited.copy()
summary["状态"] = statuses
st.dataframe(summary[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "状态"]], hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})

duplicate_blocked = sum(status == "疑似重复（未保存）" for status in statuses)
forced_duplicates = sum(status == "重复但已确认" for status in statuses)
needs_date = sum(status == "需确认日期" for status in statuses)
expense_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == EXPENSE)
income_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == INCOME)
a, b, c, d, e = st.columns(5)
a.metric("准备保存", f"{len(candidates)} 笔")
b.metric("重复待确认", f"{duplicate_blocked} 笔")
c.metric("强制重复", f"{forced_duplicates} 笔")
d.metric("日期待确认", f"{needs_date} 笔")
e.metric("净支出", money(expense_total - income_total))

reconciliation = reconcile_receipt_total(candidates, payload.get("receipt_total"))
difference_needs_confirm = False
if reconciliation:
    if reconciliation["matches"]:
        st.success(f"项目净合计 {money(reconciliation['selected_net_total'])} 与收据总额 {money(reconciliation['receipt_total'])} 一致（容差 RM {reconciliation['tolerance']:.2f}）。")
    else:
        difference_needs_confirm = True
        st.warning(f"项目净合计 {money(reconciliation['selected_net_total'])} 与收据总额 {money(reconciliation['receipt_total'])} 相差 {money(abs(reconciliation['difference']))}。请检查漏项、重复项、折扣或退款。")

confirm_difference = True
if difference_needs_confirm:
    confirm_difference = st.checkbox("我已检查收据总额差异，仍确认按当前项目保存。", key="receipt_difference_confirm")
confirm = st.checkbox(f"我已核对，并确认新增 {len(candidates)} 笔交易。", disabled=not candidates)

if st.button("保存选中项目", type="primary", use_container_width=True, disabled=not confirm or not candidates or not confirm_difference):
    try:
        latest_existing = existing_transaction_keys(fresh=True)
        final_rows, skipped = finalize_receipt_candidates(candidates, latest_existing)
        if not final_rows:
            st.warning("保存前重新检查后，没有可新增的记录。被新发现的重复项仍保留在画面中，可确认后勾选「仍然保存重复」。")
        else:
            saved = insert_transactions(final_rows)
            st.session_state.pop("receipt_result", None)
            st.toast(f"成功保存 {saved} 笔交易" + (f"，保存前跳过 {skipped} 笔新出现的重复" if skipped else ""))
            st.balloons()
            st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
