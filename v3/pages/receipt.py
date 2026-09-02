from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

V3_ROOT = Path(__file__).resolve().parents[1]
if str(V3_ROOT) not in sys.path:
    sys.path.insert(0, str(V3_ROOT))

from wywallet.access import render_lock_button, require_access, touch_access
from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, APP_VERSION, EXPENSE, REFUND, today_my
from wywallet.db import create_category, get_client, invalidate_data, normalize_transaction, transaction_duplicate_key
from wywallet.ledger_codec import physical_payload
from wywallet.receipt import (
    evaluate_receipt_candidates,
    finalize_receipt_candidates,
    materialize_receipt_adjustments,
    reconcile_receipt_total,
)
from wywallet.receipt_identity import add_line_ids, receipt_already_exists, receipt_root_id
from wywallet.snapshot import current_snapshot, fresh_snapshot
from wywallet.ui import inject_css, money, page_header
from wywallet.ux import ranked_categories

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()
require_access()
touch_access()

_DRAFT_COLUMNS = [
    "保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type",
    "amount", "note", "receipt_id", "flow_subtype",
]


def _draft_key(signature: str) -> str:
    return f"receipt_draft_{signature}"


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
        "flow_subtype": None,
    }.items():
        if column not in frame:
            frame[column] = default

    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    future = parsed_dates.notna() & (parsed_dates.dt.date > today_my())
    needs_confirmation = parsed_dates.isna() | future
    safe_dates = parsed_dates.copy()
    safe_dates.loc[needs_confirmation] = pd.Timestamp(today_my())
    frame["_date_future"] = future
    frame["_date_missing"] = needs_confirmation
    frame["date"] = safe_dates

    category_map = {str(category).casefold(): str(category) for category in categories}
    frame["category"] = frame["category"].fillna("").astype(str).map(
        lambda value: category_map.get(value.strip().casefold(), fallback_category)
    )
    frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, REFUND]), EXPENSE)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
    frame.insert(0, "仍然保存重复", False)
    frame.insert(0, "日期已确认", (~needs_confirmation).tolist())
    frame.insert(0, "保存", True)
    return frame


def _initial_draft(frame: pd.DataFrame, signature: str) -> pd.DataFrame:
    key = _draft_key(signature)
    if key not in st.session_state:
        st.session_state[key] = frame[_DRAFT_COLUMNS].to_dict("records")
    records = st.session_state.get(key) or []
    draft = pd.DataFrame(records)
    for column in _DRAFT_COLUMNS:
        if column not in draft:
            draft[column] = None
    return draft[_DRAFT_COLUMNS].copy()


def _store_draft(signature: str, frame: pd.DataFrame) -> None:
    work = frame.copy()
    for column in _DRAFT_COLUMNS:
        if column not in work:
            work[column] = None
    st.session_state[_draft_key(signature)] = work[_DRAFT_COLUMNS].to_dict("records")


def _clear_target_editor_state(signature: str, mode: str) -> None:
    if mode == "表格":
        st.session_state.pop(f"receipt_editor_release_{signature}", None)
        return
    prefixes = [
        "receipt_keep_", "receipt_date_ok_", "receipt_force_", "receipt_date_",
        "receipt_type_", "receipt_item_", "receipt_category_", "receipt_amount_", "receipt_note_",
    ]
    for key in list(st.session_state):
        if any(str(key).startswith(prefix) for prefix in prefixes) and f"_{signature}_" in str(key):
            st.session_state.pop(key, None)


def _clear_receipt_session_state(signature: str) -> None:
    """Clear all draft/editor/confirmation state tied to one receipt image."""
    if not signature:
        return
    _clear_target_editor_state(signature, "表格")
    _clear_target_editor_state(signature, "卡片")
    st.session_state.pop(_draft_key(signature), None)
    st.session_state.pop(f"receipt_last_mode_{signature}", None)
    for prefix in [
        "force_whole_receipt_",
        "receipt_difference_confirm_",
        "receipt_final_confirm_",
        "receipt_save_",
    ]:
        st.session_state.pop(f"{prefix}{signature}", None)


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
                "类型", [EXPENSE, REFUND], index=0 if row["type"] == EXPENSE else 1,
                key=f"receipt_type_{signature}_{index}",
            )
            item = st.text_input("项目／商家", value=str(row.get("item") or ""), max_chars=180, key=f"receipt_item_{signature}_{index}")
            e1, e2 = st.columns(2)
            options = list(categories)
            current_category = str(row.get("category") or "")
            if current_category and current_category not in options:
                options.insert(0, current_category)
            category = e1.selectbox(
                "类别", options, index=options.index(current_category) if current_category in options else 0,
                key=f"receipt_category_{signature}_{index}",
            )
            amount = e2.number_input(
                "金额 (RM)", min_value=0.01, step=0.01,
                value=float(row["amount"]) if not pd.isna(row["amount"]) else 0.01,
                key=f"receipt_amount_{signature}_{index}",
            )
            note = st.text_area("备注", value=str(row.get("note") or ""), max_chars=1000, key=f"receipt_note_{signature}_{index}")
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
                "flow_subtype": str(row.get("flow_subtype") or "").strip() or None,
            })
    return pd.DataFrame(edited_rows)


def _duplicate_keys(frame: pd.DataFrame) -> set[tuple]:
    keys: set[tuple] = set()
    for row in frame.to_dict("records") if not frame.empty else []:
        try:
            keys.add(transaction_duplicate_key(row))
        except ValueError:
            continue
    return keys


def _insert_receipt_rows(rows: list[dict]) -> int:
    payloads: list[dict] = []
    for row in rows:
        logical = normalize_transaction(row)
        payloads.append(physical_payload(logical, str(row.get("flow_subtype") or "")))
    if not payloads:
        raise ValueError("没有可保存的记录。")
    get_client().table("transactions").insert(payloads).execute()
    invalidate_data()
    return len(payloads)


page_header("📷 AI 收据识别", "Gemini 负责提取；最终日期、金额、重复项和保存范围全部由本地逻辑验证。")
st.caption(APP_VERSION)
st.page_link("app.py", label="← 返回 WY Wallet", width="content")
with st.sidebar:
    render_lock_button()

loading = st.empty()
loading.info("正在读取现有账本与类别…")
try:
    snap = current_snapshot()
    transactions = snap["transactions"]
    categories = ranked_categories(snap["categories"], transactions)
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
previous_signature = str(st.session_state.get("receipt_signature") or "")
if previous_signature != image_signature:
    _clear_receipt_session_state(previous_signature)
    st.session_state["receipt_signature"] = image_signature
    st.session_state.pop("receipt_result", None)
    _clear_receipt_session_state(image_signature)

preview, action = st.columns([1, 1.25], gap="large")
with preview:
    st.image(raw, caption="待识别收据", width="stretch")
with action:
    instruction = st.text_area("补充说明（可选）", placeholder="例如：这是退款单；日期实际是昨天。", max_chars=1000)
    if st.button("✨ 使用 Gemini 3.7 Flash 识别", type="primary", width="stretch"):
        try:
            mime = getattr(source, "type", None) or "image/jpeg"
            with st.spinner("正在识别收据…"):
                result = recognize_receipt(raw, mime, categories, instruction.strip())
            _clear_receipt_session_state(image_signature)
            st.session_state["receipt_result"] = result.model_dump()
            st.rerun()
        except Exception as exc:
            st.error(f"收据识别失败：{exc}")
    if st.button("清除识别结果", width="stretch"):
        st.session_state.pop("receipt_result", None)
        _clear_receipt_session_state(image_signature)
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
tax = float(payload.get("tax") or 0)
service_charge = float(payload.get("service_charge") or 0)
discount = float(payload.get("discount") or 0)
rows = materialize_receipt_adjustments(
    base_rows, tax=tax, service_charge=service_charge, discount=discount,
    fallback_category=fallback_category, receipt_id=None,
)
if tax or service_charge or discount:
    st.info(f"附加项：税 {money(tax)} · 服务费 {money(service_charge)} · 折扣 {money(discount)}。折扣会抵减净支出。")
if payload.get("merchant") or payload.get("receipt_number"):
    st.caption("收据：" + " · ".join(
        value for value in [str(payload.get("merchant") or "").strip(), str(payload.get("receipt_number") or "").strip()] if value
    ))

frame = _prepare_frame(rows, categories, fallback_category)
if bool(frame.get("_date_future", pd.Series(dtype=bool)).any()):
    st.warning("AI 识别到未来日期。系统已暂时改为今天并取消日期确认，请人工核对正确日期后再保存。")
draft = _initial_draft(frame, image_signature)

st.subheader("检查并修改")
st.caption("手机默认使用卡片编辑；日期看不清或识别成未来日期时会暂填今天，但必须人工确认。Receipt ID 会在人工修改完成后按最终内容生成。")
new_cat_col, create_col = st.columns([2, 1])
new_cat = new_cat_col.text_input("需要新类别时先建立", placeholder="例如：宠物", max_chars=80)
if create_col.button("＋ 建立类别", width="stretch"):
    try:
        created = create_category(new_cat)
        st.toast("类别已建立" if created else "类别已存在")
        st.rerun()
    except Exception as exc:
        st.error(f"建立类别失败：{exc}")

mode = st.segmented_control("编辑方式", ["卡片", "表格"], default="卡片", key="receipt_edit_mode_release")
last_mode_key = f"receipt_last_mode_{image_signature}"
last_mode = st.session_state.get(last_mode_key)
if last_mode and last_mode != mode:
    _clear_target_editor_state(image_signature, mode)
st.session_state[last_mode_key] = mode

if mode == "卡片":
    edited = _card_editor(draft, categories, image_signature)
else:
    visible = draft[_DRAFT_COLUMNS].copy()
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
            "item": st.column_config.TextColumn("项目／商家", required=True, width="large", max_chars=180),
            "category": st.column_config.SelectboxColumn("类别", options=categories, required=True),
            "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, REFUND], required=True),
            "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
            "note": st.column_config.TextColumn("备注", width="large", max_chars=1000),
            "receipt_id": None,
            "flow_subtype": None,
        },
        key=f"receipt_editor_release_{image_signature}",
    )
    edited = pd.DataFrame(edited)

# The semantic receipt identity is based on the final human-confirmed editor
# values, not the raw OCR output. This prevents a corrected date/item from
# silently creating a different receipt identity on a later scan.
identity_rows = edited.to_dict("records") if not edited.empty else []
root_id = receipt_root_id(payload, identity_rows)
edited = pd.DataFrame(add_line_ids(identity_rows, root_id))
_store_draft(image_signature, edited)

already_saved = receipt_already_exists(root_id, transactions.get("receipt_id", pd.Series(dtype=str)).tolist())
force_whole_receipt = False
if already_saved:
    st.error("检测到按最终确认内容计算出的 Receipt ID 已经存在。默认禁止整张重复入账。")
    force_whole_receipt = st.checkbox(
        "我确认这是同一张收据，但仍要再次入账。",
        key=f"force_whole_receipt_{image_signature}",
    )

if already_saved and force_whole_receipt and not edited.empty:
    edited["仍然保存重复"] = edited["保存"].fillna(False).astype(bool)

existing = _duplicate_keys(transactions)
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
a, b, c, d, e = st.columns(5, gap="small")
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
    st.checkbox(
        "我已检查总额差异，仍确认按当前项目保存。",
        key=f"receipt_difference_confirm_{image_signature}",
    )
    if difference_needs_confirm
    else True
)
confirm = st.checkbox(
    f"我已核对，并确认新增 {len(candidates)} 笔交易。",
    disabled=not candidates,
    key=f"receipt_final_confirm_{image_signature}",
)
blocked_whole = already_saved and not force_whole_receipt
if st.button(
    "保存选中项目",
    type="primary",
    width="stretch",
    disabled=not confirm or not candidates or not confirm_difference or blocked_whole,
    key=f"receipt_save_{image_signature}",
):
    try:
        latest = fresh_snapshot()["transactions"]
        if receipt_already_exists(root_id, latest.get("receipt_id", pd.Series(dtype=str)).tolist()) and not force_whole_receipt:
            st.error("保存前再次确认：这张收据已经存在，因此没有写入任何交易。")
            st.stop()
        fresh_keys = _duplicate_keys(latest)
        final_rows, skipped = finalize_receipt_candidates(candidates, fresh_keys)
        if skipped:
            st.warning("保存前发现新的重复记录。系统没有部分保存；请重新核对后再试。")
            st.stop()
        if not final_rows:
            st.warning("没有可新增的记录。")
            st.stop()
        if already_saved and force_whole_receipt:
            duplicate_root = hashlib.sha256(f"{root_id}:{uuid.uuid4()}".encode("utf-8")).hexdigest()[:16]
            final_rows = add_line_ids(final_rows, duplicate_root)
        saved = _insert_receipt_rows(final_rows)
        st.session_state.pop("receipt_result", None)
        _clear_receipt_session_state(image_signature)
        st.toast(f"成功保存 {saved} 笔交易")
        st.balloons()
        st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
