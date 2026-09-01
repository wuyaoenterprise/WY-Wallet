from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

V3_ROOT = Path(__file__).resolve().parents[1]
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, APP_VERSION, BUILD_ID, EXPENSE, REFUND, today_my
from wywallet.db import create_category, existing_transaction_keys, insert_transactions, load_categories, load_transactions
from wywallet.receipt import evaluate_receipt_candidates, finalize_receipt_candidates, materialize_receipt_adjustments, reconcile_receipt_total
from wywallet.ui import inject_css, money, page_header
from wywallet.web import render_session_controls, require_private_access, touch_access_session

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()
require_private_access()
touch_access_session()


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _receipt_identity(payload: dict, rows: list[dict]) -> str:
    merchant = _norm(payload.get("merchant"))
    number = _norm(payload.get("receipt_number"))
    total = round(float(payload.get("receipt_total") or 0), 2)
    dates = sorted({str(row.get("date") or "") for row in rows if row.get("date")})
    date_value = dates[0] if dates else ""
    if merchant and number:
        seed = {"merchant": merchant, "number": number, "date": date_value, "total": total}
    elif merchant and date_value and payload.get("receipt_total") is not None:
        seed = {"merchant": merchant, "date": date_value, "total": total}
    else:
        normalized_rows = []
        for row in rows:
            normalized_rows.append({
                "date": str(row.get("date") or ""),
                "item": _norm(row.get("item")),
                "type": str(row.get("type") or ""),
                "amount": round(float(pd.to_numeric(row.get("amount"), errors="coerce") or 0), 2),
            })
        normalized_rows.sort(key=lambda r: (r["date"], r["item"], r["type"], r["amount"]))
        seed = {"rows": normalized_rows, "total": total, "merchant": merchant}
    return hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _prepare_frame(rows: list[dict], categories: list[str], fallback_category: str, receipt_id: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column, default in {"date": None, "item": "", "category": fallback_category, "type": EXPENSE, "amount": None, "note": "", "receipt_id": receipt_id}.items():
        if column not in frame: frame[column] = default
    frame["receipt_id"] = receipt_id
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    frame["_date_missing"] = parsed_dates.isna()
    frame["date"] = parsed_dates.fillna(pd.Timestamp(today_my()))
    category_map = {str(category).casefold(): str(category) for category in categories}
    frame["category"] = frame["category"].fillna("").astype(str).map(lambda value: category_map.get(value.strip().casefold(), fallback_category))
    frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, REFUND]), EXPENSE)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame.insert(0, "仍然保存重复", False)
    frame.insert(0, "日期已确认", (~frame["_date_missing"]).tolist())
    frame.insert(0, "保存", True)
    return frame


def _card_editor(frame: pd.DataFrame, categories: list[str], signature: str) -> pd.DataFrame:
    edited_rows: list[dict] = []
    for idx, row in frame.reset_index(drop=True).iterrows():
        with st.container(border=True):
            title = str(row.get("item") or f"项目 {idx + 1}")
            st.markdown(f"**{idx + 1}. {title}**")
            a, b, c = st.columns(3)
            keep = a.checkbox("保存", value=bool(row.get("保存", True)), key=f"rc_keep_{signature}_{idx}")
            date_ok = b.checkbox("日期已确认", value=bool(row.get("日期已确认", False)), key=f"rc_dateok_{signature}_{idx}")
            force = c.checkbox("仍然保存重复", value=bool(row.get("仍然保存重复", False)), key=f"rc_force_{signature}_{idx}")
            d1, d2 = st.columns(2)
            tx_date = d1.date_input("日期", value=pd.to_datetime(row["date"]).date(), max_value=today_my(), key=f"rc_date_{signature}_{idx}")
            tx_type = d2.selectbox("类型", [EXPENSE, REFUND], index=0 if row["type"] == EXPENSE else 1, key=f"rc_type_{signature}_{idx}")
            item = st.text_input("项目／商家", value=str(row.get("item") or ""), key=f"rc_item_{signature}_{idx}")
            e1, e2 = st.columns(2)
            category = e1.selectbox("类别", categories, index=categories.index(row["category"]) if row["category"] in categories else 0, key=f"rc_cat_{signature}_{idx}")
            amount = e2.number_input("金额 (RM)", min_value=0.01, step=0.01, value=float(row["amount"]) if not pd.isna(row["amount"]) else 0.01, key=f"rc_amt_{signature}_{idx}")
            note = st.text_area("备注", value=str(row.get("note") or ""), key=f"rc_note_{signature}_{idx}")
            edited_rows.append({"保存": keep, "日期已确认": date_ok, "仍然保存重复": force, "date": tx_date, "item": item, "category": category, "type": tx_type, "amount": amount, "note": note, "receipt_id": row.get("receipt_id", "")})
    return pd.DataFrame(edited_rows)


page_header("📷 AI 收据识别", "Gemini 3.7 Flash 负责提取；日期、退款、重复项与最终金额全部由本地逻辑再次验证。")
st.caption(f"{APP_VERSION} · {BUILD_ID}")
st.page_link("app.py", label="← 返回 WY Wallet", width="content")
with st.sidebar:
    render_session_controls()

try:
    transactions = load_transactions()
    categories = load_categories(transactions)
except Exception as exc:
    st.error(f"无法读取 Supabase：{exc}"); st.stop()

source_mode = st.segmented_control("图片来源", ["上传图片", "直接拍照"], default="上传图片", key="receipt_source_mode")
source = st.camera_input("拍摄收据", key="receipt_camera_v7") if source_mode == "直接拍照" else st.file_uploader("上传 JPG、PNG 或 WebP", type=["jpg", "jpeg", "png", "webp"], key="receipt_upload_v7")
if source is None:
    st.info("选择图片或拍照后即可识别。"); st.stop()
raw = source.getvalue()
if len(raw) > 10 * 1024 * 1024:
    st.error("图片超过 10 MB，请压缩或重新拍摄后再识别。"); st.stop()

image_signature = hashlib.sha256(raw).hexdigest()[:20]
if st.session_state.get("receipt_signature") != image_signature:
    st.session_state["receipt_signature"] = image_signature
    st.session_state.pop("receipt_result", None)

preview, action = st.columns([1, 1.25], gap="large")
with preview: st.image(raw, caption="待识别收据", width="stretch")
with action:
    instruction = st.text_area("补充说明（可选）", placeholder="例如：这是退款单；日期实际是昨天。")
    if st.button("✨ 使用 Gemini 3.7 Flash 识别", type="primary", width="stretch"):
        try:
            mime = getattr(source, "type", None) or "image/jpeg"
            with st.spinner("正在读取收据并拆分项目…"): result = recognize_receipt(raw, mime, categories, instruction.strip())
            st.session_state["receipt_result"] = result.model_dump(); st.rerun()
        except Exception as exc: st.error(f"收据识别失败：{exc}")
    if st.button("清除识别结果", width="stretch"): st.session_state.pop("receipt_result", None); st.rerun()

payload = st.session_state.get("receipt_result")
if not payload: st.stop()
for warning in payload.get("warnings") or []: st.warning(str(warning))
rows = payload.get("transactions") or []
if not rows: st.warning("AI 没有识别到可用交易，请换更清晰的图片。"); st.stop()

fallback_category = "其他" if "其他" in categories else (categories[0] if categories else "其他")
receipt_id = _receipt_identity(payload, rows)
tax, service_charge, discount = float(payload.get("tax") or 0), float(payload.get("service_charge") or 0), float(payload.get("discount") or 0)
rows = materialize_receipt_adjustments(rows, tax=tax, service_charge=service_charge, discount=discount, fallback_category=fallback_category, receipt_id=receipt_id)
if tax or service_charge or discount: st.info(f"AI 识别附加项：税 {money(tax)} · 服务费 {money(service_charge)} · 折扣 {money(discount)}。Receipt ID 隐藏保存，不会污染项目名称。")
if payload.get("merchant") or payload.get("receipt_number"): st.caption("收据识别：" + " · ".join(v for v in [str(payload.get("merchant") or "").strip(), str(payload.get("receipt_number") or "").strip()] if v))

frame = _prepare_frame(rows, categories, fallback_category, receipt_id)
st.subheader("检查并修改")
st.caption("日期看不清时会暂填今天但必须人工确认。手机建议用卡片模式；桌面可切换表格模式。")
new_cat_col, create_col = st.columns([2, 1]); new_cat = new_cat_col.text_input("需要新类别时先建立", placeholder="例如：宠物")
if create_col.button("＋ 建立类别", width="stretch"):
    try: created = create_category(new_cat); st.toast("类别已建立" if created else "类别已存在"); st.rerun()
    except Exception as exc: st.error(f"建立类别失败：{exc}")

edit_mode = st.segmented_control("编辑方式", ["卡片", "表格"], default="卡片", key="receipt_edit_mode")
if edit_mode == "卡片":
    edited = _card_editor(frame, categories, image_signature)
else:
    visible = frame[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "note"]].copy()
    edited = st.data_editor(visible, hide_index=True, width="stretch", num_rows="dynamic", column_config={
        "保存": st.column_config.CheckboxColumn("保存"), "日期已确认": st.column_config.CheckboxColumn("日期已确认"), "仍然保存重复": st.column_config.CheckboxColumn("仍然保存重复"),
        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True, max_value=today_my()), "item": st.column_config.TextColumn("项目／商家", required=True, width="large"), "category": st.column_config.SelectboxColumn("类别", options=categories, required=True), "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, REFUND], required=True), "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True), "note": st.column_config.TextColumn("备注", width="large"),
    }, key=f"receipt_editor_{image_signature}")
    edited["receipt_id"] = receipt_id

# Editing uses cached shared keys for speed. Only the final save performs a fresh full duplicate check.
existing = existing_transaction_keys(fresh=False)
statuses, candidates = evaluate_receipt_candidates(edited, existing)
summary = edited.copy(); summary["状态"] = statuses
st.dataframe(summary[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "状态"]], hide_index=True, width="stretch", column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})

duplicate_blocked = sum(status == "疑似重复（未保存）" for status in statuses); needs_date = sum(status == "需确认日期" for status in statuses); expense_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == EXPENSE); refund_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == REFUND)
a, b, c = st.columns(3); d, e = st.columns(2); a.metric("准备保存", f"{len(candidates)} 笔"); b.metric("重复待确认", f"{duplicate_blocked} 笔"); c.metric("日期待确认", f"{needs_date} 笔"); d.metric("退款／折扣", money(refund_total)); e.metric("净支出", money(expense_total - refund_total))

reconciliation = reconcile_receipt_total(candidates, payload.get("receipt_total")); difference_needs_confirm = False
if reconciliation:
    st.caption(f"当前选中交易净合计：{money(reconciliation['expected_total'])}")
    if reconciliation["matches"]: st.success(f"准备保存的账本金额与收据总额 {money(reconciliation['receipt_total'])} 一致（容差 RM {reconciliation['tolerance']:.2f}）。")
    else: difference_needs_confirm = True; st.warning(f"准备保存的账本金额与收据总额 {money(reconciliation['receipt_total'])} 相差 {money(abs(reconciliation['difference']))}。请检查漏项、附加费用、折扣或退款方向。")
confirm_difference = st.checkbox("我已检查总额差异，仍确认按当前项目保存。", key="receipt_difference_confirm_v32") if difference_needs_confirm else True
confirm = st.checkbox(f"我已核对，并确认新增 {len(candidates)} 笔交易。", disabled=not candidates)
if st.button("保存选中项目", type="primary", width="stretch", disabled=not confirm or not candidates or not confirm_difference):
    try:
        latest_existing = existing_transaction_keys(fresh=True)
        final_rows, skipped = finalize_receipt_candidates(candidates, latest_existing)
        if skipped:
            st.warning("保存前发现新的重复记录。为避免部分保存，系统没有写入任何一笔；请重新核对后再保存。")
            st.stop()
        if not final_rows:
            st.warning("保存前重新检查后，没有可新增的记录。"); st.stop()
        saved = insert_transactions(final_rows); st.session_state.pop("receipt_result", None); st.toast(f"成功保存 {saved} 笔交易"); st.balloons(); st.rerun()
    except Exception as exc: st.error(f"保存失败：{exc}")
