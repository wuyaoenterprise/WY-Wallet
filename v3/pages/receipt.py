from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

V3_ROOT = Path(__file__).resolve().parents[1]
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

from wywallet.access import render_lock_button, require_access, touch_access
from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, APP_VERSION, BUILD_ID, EXPENSE, REFUND, today_my
from wywallet.db import (
    create_category,
    existing_transaction_keys,
    fetch_transactions_interactive_fresh,
    insert_transactions,
    load_categories,
    load_transactions,
    transaction_duplicate_key,
)
from wywallet.receipt import (
    evaluate_receipt_candidates,
    finalize_receipt_candidates,
    materialize_receipt_adjustments,
    reconcile_receipt_total,
)
from wywallet.receipt_identity import add_line_ids, receipt_already_exists, receipt_root_id
from wywallet.ui import inject_css, money, page_header

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()
require_access()
touch_access()


def _prepare_frame(rows: list[dict], categories: list[str], fallback_category: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column, default in {
        "date": None,
        "item": "",
        "category": fallback_category,
        "type": EXPENSE,
        "amount": None,
        "note": "",
        "receipt_id": "",
    }.items():
        if column not in frame:
            frame[column] = default
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    frame["_date_missing"] = parsed_dates.isna()
    frame["date"] = parsed_dates.fillna(pd.Timestamp(today_my()))
    category_map = {str(category).casefold(): str(category) for category in categories}
    frame["category"] = frame["category"].fillna("").astype(str).map(
        lambda value: category_map.get(value.strip().casefold(), fallback_category)
    )
    frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, REFUND]), EXPENSE)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame.insert(0, "仍然保存重复", False)
    frame.insert(0, "日期已确认", (~frame["_date_missing"]).tolist())
    frame.insert(0, "保存", True)
    return frame


def _card_editor(frame: pd.DataFrame, categories: list[str], signature: str) -> pd.DataFrame:
    edited_rows: list[dict] = []
    for index, row in frame.reset_index(drop=True).iterrows():
        with st.container(border=True):
            st.markdown(f"**{index + 1}. {str(row.get('item') or f'项目 {index + 1}')}**")
            a, b, c = st.columns(3)
            keep = a.checkbox("保存", value=bool(row.get("保存", True)), key=f"receipt_keep_{signature}_{index}")
            date_ok = b.checkbox("日期已确认", value=bool(row.get("日期已确认", False)), key=f"receipt_date_ok_{signature}_{index}")
            force = c.checkbox("仍然保存重复", value=bool(row.get("仍然保存重复", False)), key=f"receipt_force_{signature}_{index}")
            d1, d2 = st.columns(2)
            tx_date = d1.date_input(
                "日期",
                value=pd.to_datetime(row["date"]).date(),
                max_value=today_my(),
                key=f"receipt_date_{signature}_{index}",
            )
            tx_type = d2.selectbox(
                "类型",
                [EXPENSE, REFUND],
                index=0 if row["type"] == EXPENSE else 1,
                key=f"receipt_type_{signature}_{index}",
            )
            item = st.text_input("项目／商家", value=str(row.get("item") or ""), key=f"receipt_item_{signature}_{index}")
            e1, e2 = st.columns(2)
            category = e1.selectbox(
                "类别",
                categories,
                index=categories.index(row["category"]) if row["category"] in categories else 0,
                key=f"receipt_category_{signature}_{index}",
            )
            amount = e2.number_input(
                "金额 (RM)",
                min_value=0.01,
                step=0.01,
                value=float(row["amount"]) if not pd.isna(row["amount"]) else 0.01,
                key=f"receipt_amount_{signature}_{index}",
            )
            note = st.text_area("备注", value=str(row.get("note") or ""), key=f"receipt_note_{signature}_{index}")
            edited_rows.append({
                "保存": keep,
                "日期已确认": date_ok,
                "仍然保存重复": force,
                "date": tx_date,
                "item": item,
                "category": category,
                "type": tx_type,
                "amount": amount,
                "note": note,
                "receipt_id": str(row.get("receipt_id") or ""),
            })
    return pd.DataFrame(edited_rows)


page_header("📷 AI 收据识别", "Gemini 负责提取；最终日期、金额、重复项和保存范围全部由本地逻辑验证。")
st.caption(f"{APP_VERSION} · {BUILD_ID}")
st.page_link("app.py", label="← 返回 WY Wallet", width="content")
with st.sidebar:
    render_lock_button()

loading = st.empty()
loading.info("正在读取现有账本与类别…")
try:
    transactions = load_transactions()
    categories = load_categories(transactions)
except Exception as exc:
    loading.empty()
    st.error(f"无法读取 Supabase：{exc}")
    st.stop()
loading.empty()

source_mode = st.segmented_control("图片来源", ["上传图片", "直接拍照"], default="上传图片", key="receipt_source_mode_release")
source = (
    st.camera_input("拍摄收据", key="receipt_camera_release")
    if source_mode == "直接拍照"
    else st.file_uploader("上传 JPG、PNG 或 WebP", type=["jpg", "jpeg", "png", "webp"], key="receipt_upload_release")
)
if source is None:
    st.info("选择图片或拍照后即可识别。")
    st.stop()
raw = source.getvalue()
if len(raw) > 10 * 1024 * 1024:
    st.error("图片超过 10 MB，请压缩或重新拍摄。")
    st.stop()

image_signature = hashlib.sha256(raw).hexdigest()[:20]
if st.session_state.get("receipt_signature") != image_signature:
    st.session_state["receipt_signature"] = image_signature
    st.session_state.pop("receipt_result", None)

preview, action = st.columns([1, 1.25], gap="large")
with preview:
    st.image(raw, caption="待识别收据", width="stretch")
with action:
    instruction = st.text_area("补充说明（可选）", placeholder="例如：这是退款单；日期实际是昨天。")
    if st.button("✨ 使用 Gemini 3.7 Flash 识别", type="primary", width="stretch"):
        try:
            mime = getattr(source, "type", None) or "image/jpeg"
            with st.spinner("正在识别收据…"):
                result = recognize_receipt(raw, mime, categories, instruction.strip())
            st.session_state["receipt_result"] = result.model_dump()
            st.rerun()
        except Exception as exc:
            st.error(f"收据识别失败：{exc}")
    if st.button("清除识别结果", width="stretch"):
        st.session_state.pop("receipt_result", None)
        st.rerun()

payload = st.session_state.get("receipt_result")
if not payload:
    st.stop()
for warning in payload.get("warnings") or []:
    st.warning(str(warning))
base_rows = payload.get("transactions") or []
if not base_rows:
    st.warning("AI 没有识别到可用交易，请换更清晰的图片。")
    st.stop()

fallback_category = "其他" if "其他" in categories else (categories[0] if categories else "其他")
root_id = receipt_root_id(payload, base_rows)
already_saved = receipt_already_exists(root_id, transactions.get("receipt_id", pd.Series(dtype=str)).tolist())
force_whole_receipt = False
if already_saved:
    st.error("检测到这张收据的 Receipt ID 已经存在。默认禁止整张重复入账。")
    force_whole_receipt = st.checkbox("我确认这是同一张收据，但仍要再次入账。", key="force_whole_receipt")

tax = float(payload.get("tax") or 0)
service_charge = float(payload.get("service_charge") or 0)
discount = float(payload.get("discount") or 0)
rows = materialize_receipt_adjustments(
    base_rows,
    tax=tax,
    service_charge=service_charge,
    discount=discount,
    fallback_category=fallback_category,
    receipt_id=root_id,
)
rows = add_line_ids(rows, root_id)
if tax or service_charge or discount:
    st.info(f"附加项：税 {money(tax)} · 服务费 {money(service_charge)} · 折扣 {money(discount)}。折扣会抵减净支出。")
if payload.get("merchant") or payload.get("receipt_number"):
    st.caption("收据：" + " · ".join(
        value for value in [str(payload.get("merchant") or "").strip(), str(payload.get("receipt_number") or "").strip()] if value
    ))

frame = _prepare_frame(rows, categories, fallback_category)
st.subheader("检查并修改")
st.caption("手机默认使用卡片编辑；日期看不清时会暂填今天，但必须人工确认。")
new_cat_col, create_col = st.columns([2, 1])
new_cat = new_cat_col.text_input("需要新类别时先建立", placeholder="例如：宠物")
if create_col.button("＋ 建立类别", width="stretch"):
    try:
        created = create_category(new_cat)
        st.toast("类别已建立" if created else "类别已存在")
        st.rerun()
    except Exception as exc:
        st.error(f"建立类别失败：{exc}")

mode = st.segmented_control("编辑方式", ["卡片", "表格"], default="卡片", key="receipt_edit_mode_release")
if mode == "卡片":
    edited = _card_editor(frame, categories, image_signature)
else:
    visible = frame[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "note", "receipt_id"]].copy()
    edited = st.data_editor(
        visible,
        hide_index=True,
        width="stretch",
        num_rows="dynamic",
        column_config={
            "保存": st.column_config.CheckboxColumn("保存"),
            "日期已确认": st.column_config.CheckboxColumn("日期已确认"),
            "仍然保存重复": st.column_config.CheckboxColumn("仍然保存重复"),
            "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True, max_value=today_my()),
            "item": st.column_config.TextColumn("项目／商家", required=True, width="large"),
            "category": st.column_config.SelectboxColumn("类别", options=categories, required=True),
            "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, REFUND], required=True),
            "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
            "note": st.column_config.TextColumn("备注", width="large"),
            "receipt_id": None,
        },
        key=f"receipt_editor_release_{image_signature}",
    )
    edited = pd.DataFrame(add_line_ids(edited.to_dict("records"), root_id))

existing = existing_transaction_keys(fresh=False)
statuses, candidates = evaluate_receipt_candidates(edited, existing)
summary = edited.copy()
summary["状态"] = statuses
st.dataframe(
    summary[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "状态"]],
    hide_index=True,
    width="stretch",
    column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")},
)

duplicate_blocked = sum(status == "疑似重复（未保存）" for status in statuses)
needs_date = sum(status == "需确认日期" for status in statuses)
expense_total = sum(candidate.normalized["amount"] for candidate in candidates if candidate.normalized["type"] == EXPENSE)
refund_total = sum(candidate.normalized["amount"] for candidate in candidates if candidate.normalized["type"] == REFUND)
a, b, c = st.columns(3)
d, e = st.columns(2)
a.metric("准备保存", f"{len(candidates)} 笔")
b.metric("重复待确认", f"{duplicate_blocked} 笔")
c.metric("日期待确认", f"{needs_date} 笔")
d.metric("退款／折扣", money(refund_total))
e.metric("净支出", money(expense_total - refund_total))

reconciliation = reconcile_receipt_total(candidates, payload.get("receipt_total"))
difference_needs_confirm = False
if reconciliation:
    if reconciliation["matches"]:
        st.success(f"当前账本合计与收据总额 {money(reconciliation['receipt_total'])} 一致。")
    else:
        difference_needs_confirm = True
        st.warning(
            f"准备保存的账本金额与收据总额 {money(reconciliation['receipt_total'])} 相差 "
            f"{money(abs(reconciliation['difference']))}。请检查漏项、附加费用、折扣或退款方向。"
        )
confirm_difference = (
    st.checkbox("我已检查总额差异，仍确认按当前项目保存。", key="receipt_difference_confirm_release")
    if difference_needs_confirm
    else True
)
confirm = st.checkbox(f"我已核对，并确认新增 {len(candidates)} 笔交易。", disabled=not candidates)
blocked_whole = already_saved and not force_whole_receipt
if st.button(
    "保存选中项目",
    type="primary",
    width="stretch",
    disabled=not confirm or not candidates or not confirm_difference or blocked_whole,
):
    try:
        latest, _, _ = fetch_transactions_interactive_fresh()
        if receipt_already_exists(root_id, latest.get("receipt_id", pd.Series(dtype=str)).tolist()) and not force_whole_receipt:
            st.error("保存前再次确认：这张收据已经存在，因此没有写入任何交易。")
            st.stop()
        fresh_keys = set()
        for row in latest.to_dict("records") if not latest.empty else []:
            try:
                fresh_keys.add(transaction_duplicate_key(row))
            except ValueError:
                pass
        final_rows, skipped = finalize_receipt_candidates(candidates, fresh_keys)
        if skipped:
            st.warning("保存前发现新的重复记录。系统没有部分保存；请重新核对后再试。")
            st.stop()
        if not final_rows:
            st.warning("没有可新增的记录。")
            st.stop()
        saved = insert_transactions(final_rows)
        st.session_state.pop("receipt_result", None)
        st.toast(f"成功保存 {saved} 笔交易")
        st.balloons()
        st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
