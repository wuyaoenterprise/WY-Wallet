from __future__ import annotations

import hashlib
import hmac
import time

import pandas as pd
import streamlit as st

from wywallet.ai import recognize_receipt
from wywallet.config import APP_TITLE, APP_VERSION, BUILD_ID, EXPENSE, REFUND, today_my
from wywallet.db import create_category, existing_transaction_keys, insert_transactions, load_categories, load_transactions
from wywallet.receipt import evaluate_receipt_candidates, finalize_receipt_candidates, materialize_receipt_adjustments, reconcile_receipt_total
from wywallet.ui import inject_css, money, page_header

st.set_page_config(page_title=f"AI 收据识别 · {APP_TITLE}", page_icon="📷", layout="wide")
inject_css()


def _truthy_secret(name: str) -> bool:
    try:
        return str(st.secrets.get(name, "") or "").strip().casefold() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def _require_access() -> None:
    try:
        access_password = str(st.secrets.get("WEB_ACCESS_PASSWORD", "") or "")
    except Exception:
        access_password = ""
    if not access_password:
        if _truthy_secret("ALLOW_UNPROTECTED_ACCESS"):
            return
        page_header("安全设置未完成", "为了避免公开 URL 直接暴露财务数据，请先在 Streamlit Secrets 配置 WEB_ACCESS_PASSWORD。若 App 已由平台设为 Private，可明确设置 ALLOW_UNPROTECTED_ACCESS = true。")
        st.stop()
    if st.session_state.get("web_access_ok"):
        return
    lock_until = float(st.session_state.get("web_access_lock_until", 0) or 0)
    remaining = int(max(lock_until - time.time(), 0))
    page_header("WY Wallet 私人访问", "AI 收据页面使用与主站相同的访问保护。")
    if remaining > 0:
        st.error(f"连续输错次数过多，请 {remaining} 秒后再试。")
        st.stop()
    entered = st.text_input("访问密码", type="password", key="receipt_access_password")
    if st.button("进入", type="primary", width="stretch", key="receipt_access_submit"):
        if hmac.compare_digest(entered, access_password):
            st.session_state["web_access_ok"] = True
            st.session_state.pop("web_access_fail_count", None)
            st.session_state.pop("web_access_lock_until", None)
            st.rerun()
        else:
            count = int(st.session_state.get("web_access_fail_count", 0)) + 1
            if count >= 5:
                st.session_state["web_access_fail_count"] = 0
                st.session_state["web_access_lock_until"] = time.time() + 30
                st.error("密码连续错误 5 次，已暂时锁定 30 秒。")
            else:
                st.session_state["web_access_fail_count"] = count
                st.error(f"密码不正确。还可尝试 {5 - count} 次后进入短暂冷却。")
    st.stop()


_require_access()
page_header("📷 AI 收据识别", "Gemini 3.7 Flash 负责提取；退款、税费、服务费、折扣、日期、重复项和总额都由本地逻辑再次验证。")
st.caption(f"{APP_VERSION} · {BUILD_ID}")
st.page_link("app.py", label="← 返回 WY Wallet", width="content")

try:
    transactions = load_transactions()
    categories = load_categories(transactions)
except Exception as exc:
    st.error(f"无法读取 Supabase：{exc}")
    st.stop()

source_mode = st.segmented_control("图片来源", ["上传图片", "直接拍照"], default="上传图片", key="receipt_source_mode")
if source_mode == "直接拍照":
    source = st.camera_input("拍摄收据", key="receipt_camera_v6")
else:
    source = st.file_uploader("上传 JPG、PNG 或 WebP", type=["jpg", "jpeg", "png", "webp"], key="receipt_upload_v6")
if source is None:
    st.info("选择图片或拍照后即可识别。")
    st.stop()

raw = source.getvalue()
if len(raw) > 10 * 1024 * 1024:
    st.error("图片超过 10 MB，请压缩或重新拍摄后再识别。")
    st.stop()

signature = hashlib.sha256(raw).hexdigest()[:20]
if st.session_state.get("receipt_signature") != signature:
    st.session_state["receipt_signature"] = signature
    st.session_state.pop("receipt_result", None)

preview, action = st.columns([1, 1.25], gap="large")
with preview:
    st.image(raw, caption="待识别收据", width="stretch")
with action:
    instruction = st.text_area("补充说明（可选）", placeholder="例如：这是退款单；日期实际是昨天。")
    if st.button("✨ 使用 Gemini 3.7 Flash 识别", type="primary", width="stretch"):
        try:
            mime = getattr(source, "type", None) or "image/jpeg"
            with st.spinner("正在读取收据并拆分项目…"):
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
rows = payload.get("transactions") or []
if not rows:
    st.warning("AI 没有识别到可用交易，请换更清晰的图片。")
    st.stop()

fallback_category = "其他" if "其他" in categories else (categories[0] if categories else "其他")
tax = float(payload.get("tax") or 0)
service_charge = float(payload.get("service_charge") or 0)
discount = float(payload.get("discount") or 0)
rows = materialize_receipt_adjustments(
    rows, tax=tax, service_charge=service_charge, discount=discount,
    fallback_category=fallback_category, receipt_id=signature,
)
if tax or service_charge or discount:
    st.info(f"AI 识别附加项：税 {money(tax)} · 服务费 {money(service_charge)} · 折扣 {money(discount)}。附加行带本收据识别码，避免同日其他收据的相同税额被误判为重复。")

frame = pd.DataFrame(rows)
for column, default in {"date": None, "item": "", "category": "其他", "type": EXPENSE, "amount": None, "note": ""}.items():
    if column not in frame:
        frame[column] = default
parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
date_missing = parsed_dates.isna()
frame["date"] = parsed_dates.fillna(pd.Timestamp(today_my()))
category_map = {str(category).casefold(): str(category) for category in categories}
frame["category"] = frame["category"].fillna("").astype(str).map(lambda value: category_map.get(value.strip().casefold(), fallback_category))
frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, REFUND]), EXPENSE)
frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
frame.insert(0, "仍然保存重复", False)
frame.insert(0, "日期已确认", (~date_missing).tolist())
frame.insert(0, "保存", True)

st.subheader("检查并修改")
st.caption("看不清日期时会暂填今天但必须人工确认；退款和折扣使用 Refund 抵减支出。税费、服务费和折扣已进入编辑表，可修改或取消。")
new_cat_col, create_col = st.columns([2, 1])
new_cat = new_cat_col.text_input("需要新类别时先建立", placeholder="例如：宠物")
if create_col.button("＋ 建立类别", width="stretch"):
    try:
        created = create_category(new_cat)
        st.toast("类别已建立" if created else "类别已存在")
        st.rerun()
    except Exception as exc:
        st.error(f"建立类别失败：{exc}")

edited = st.data_editor(
    frame[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "note"]],
    hide_index=True, width="stretch", num_rows="dynamic",
    column_config={
        "保存": st.column_config.CheckboxColumn("保存"),
        "日期已确认": st.column_config.CheckboxColumn("日期已确认", help="AI 无法读出日期时必须由你确认后才能保存"),
        "仍然保存重复": st.column_config.CheckboxColumn("仍然保存重复", help="只有确认两笔相同交易都真实存在时才勾选"),
        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True, max_value=today_my()),
        "item": st.column_config.TextColumn("项目／商家", required=True, width="large"),
        "category": st.column_config.SelectboxColumn("类别", options=categories, required=True),
        "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, REFUND], required=True),
        "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
        "note": st.column_config.TextColumn("备注", width="large"),
    }, key=f"receipt_editor_{signature}",
)

existing = existing_transaction_keys()
statuses, candidates = evaluate_receipt_candidates(edited, existing)
summary = edited.copy(); summary["状态"] = statuses
st.dataframe(summary[["保存", "日期已确认", "仍然保存重复", "date", "item", "category", "type", "amount", "状态"]], hide_index=True, width="stretch", column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f")})

duplicate_blocked = sum(status == "疑似重复（未保存）" for status in statuses)
needs_date = sum(status == "需确认日期" for status in statuses)
expense_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == EXPENSE)
refund_total = sum(c.normalized["amount"] for c in candidates if c.normalized["type"] == REFUND)
a, b, c = st.columns(3); d, e = st.columns(2)
a.metric("准备保存", f"{len(candidates)} 笔"); b.metric("重复待确认", f"{duplicate_blocked} 笔"); c.metric("日期待确认", f"{needs_date} 笔")
d.metric("退款／折扣", money(refund_total)); e.metric("净支出", money(expense_total - refund_total))

reconciliation = reconcile_receipt_total(candidates, payload.get("receipt_total"))
difference_needs_confirm = False
if reconciliation:
    st.caption(f"当前选中交易净合计：{money(reconciliation['expected_total'])}")
    if reconciliation["matches"]:
        st.success(f"准备保存的账本金额与收据总额 {money(reconciliation['receipt_total'])} 一致（容差 RM {reconciliation['tolerance']:.2f}）。")
    else:
        difference_needs_confirm = True
        st.warning(f"准备保存的账本金额与收据总额 {money(reconciliation['receipt_total'])} 相差 {money(abs(reconciliation['difference']))}。请检查漏项、附加费用、折扣或退款方向。")

confirm_difference = True
if difference_needs_confirm:
    confirm_difference = st.checkbox("我已检查总额差异，仍确认按当前项目保存。", key="receipt_difference_confirm_v3")
confirm = st.checkbox(f"我已核对，并确认新增 {len(candidates)} 笔交易。", disabled=not candidates)
if st.button("保存选中项目", type="primary", width="stretch", disabled=not confirm or not candidates or not confirm_difference):
    try:
        latest_existing = existing_transaction_keys(fresh=True)
        final_rows, skipped = finalize_receipt_candidates(candidates, latest_existing)
        if not final_rows:
            st.warning("保存前重新检查后，没有可新增的记录。新发现的重复项仍保留在画面中，可确认后勾选「仍然保存重复」。")
        else:
            saved = insert_transactions(final_rows)
            st.session_state.pop("receipt_result", None)
            st.toast(f"成功保存 {saved} 笔交易" + (f"，保存前跳过 {skipped} 笔新出现的重复" if skipped else ""))
            st.balloons(); st.rerun()
    except Exception as exc:
        st.error(f"保存失败：{exc}")
