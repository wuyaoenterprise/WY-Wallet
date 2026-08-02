"""Dedicated AI receipt-recognition page for WY Wallet V2.

This page is intentionally separate from historical-data import. It accepts a
receipt image or camera photo, asks Gemini to split the receipt into candidate
transactions, and requires review before writing anything to Supabase.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import google.generativeai as genai
import pandas as pd
import streamlit as st
from PIL import Image
from supabase import create_client


MODEL_ID = "gemini-3.6-flash"
EXPENSE = "Expense"
INCOME = "Income"
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
PAGE_KEY = "receipt_recognition_v2"


st.set_page_config(page_title="AI 收据识别 · WY Wallet V2", page_icon="📷", layout="wide")
st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 1240px; padding-top: 1.15rem; padding-bottom: 3rem;
    }
    .receipt-title {font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin-bottom:.15rem}
    .receipt-subtitle {opacity:.72;margin-bottom:1rem}
    .receipt-step {font-size:1.05rem;font-weight:760;margin:.45rem 0 .65rem}
    .receipt-callout {border-left:3px solid #5b8ff9;padding:.72rem .95rem;background:rgba(91,143,249,.08);border-radius:0 10px 10px 0;margin:.55rem 0 1rem}
    div[data-testid="stMetric"] {border:1px solid rgba(128,128,128,.24);border-radius:14px;padding:.8rem 1rem;background:rgba(127,127,127,.035)}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
except Exception as exc:
    st.error(f"配置加载失败：{exc}")
    st.stop()


@st.cache_data(ttl=1800, show_spinner=False)
def load_categories() -> list[str]:
    try:
        rows = supabase.table("categories").select("name").execute().data
        categories = sorted({str(row.get("name", "")).strip() for row in rows if str(row.get("name", "")).strip()})
        return categories or DEFAULT_CATEGORIES.copy()
    except Exception:
        return DEFAULT_CATEGORIES.copy()


@st.cache_data(ttl=300, show_spinner=False)
def load_existing_keys() -> set[tuple[str, str, str, str, float]]:
    rows: list[dict[str, Any]] = []
    batch_size = 1000
    offset = 0
    try:
        while offset < 100_000:
            response = (
                supabase.table("transactions")
                .select("date,item,category,type,amount")
                .range(offset, offset + batch_size - 1)
                .execute()
            )
            batch = list(response.data or [])
            rows.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
    except Exception:
        return set()

    keys = set()
    for row in rows:
        parsed_date = pd.to_datetime(row.get("date"), errors="coerce")
        amount = pd.to_numeric(row.get("amount"), errors="coerce")
        if pd.isna(parsed_date) or pd.isna(amount):
            continue
        keys.add(
            (
                parsed_date.date().isoformat(),
                str(row.get("item") or "").strip().casefold(),
                str(row.get("category") or "").strip().casefold(),
                str(row.get("type") or EXPENSE),
                round(float(amount), 2),
            )
        )
    return keys


def clean_json_payload(text: str) -> Any:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        array_start, array_end = cleaned.find("["), cleaned.rfind("]")
        if array_start >= 0 and array_end > array_start:
            return json.loads(cleaned[array_start : array_end + 1])
        object_start, object_end = cleaned.find("{"), cleaned.rfind("}")
        if object_start >= 0 and object_end > object_start:
            return json.loads(cleaned[object_start : object_end + 1])
        raise


def analyze_receipt(image: Image.Image, categories: list[str], instructions: str) -> list[dict[str, Any]]:
    prompt = f"""
你是私人记账应用的收据识别助手。请读取图片中的真实收据，并逐项拆分成交易记录。

只返回 JSON 数组，不要 Markdown，不要解释。每项格式：
{{
  "date": "YYYY-MM-DD",
  "item": "简洁项目或商家名称",
  "category": "类别",
  "type": "Expense",
  "amount": 10.50,
  "note": "必要的原始说明"
}}

规则：
1. category 必须从以下现有类别选择：{categories}。无法判断时使用“其他”。
2. amount 必须是该项目实际支付金额的正数，不包含 RM 或其他符号。
3. 不要把小计、总计、找零、税额汇总、付款方式、卡号或余额单独建立为项目。
4. 折扣应反映到相关项目金额；不要重复计算总额。
5. 若收据只有总额而没有可靠明细，只建立一笔以商家为 item 的交易。
6. 日期无法读取时使用 {date.today().isoformat()}，并在 note 写“日期待确认”。
7. 普通购买均为 Expense；只有图片明确显示退款时才使用 Income。
8. 不要补写图片中不存在的项目或金额。
9. 用户补充说明：{instructions or '无'}。
""".strip()

    model = genai.GenerativeModel(MODEL_ID)
    response = model.generate_content([prompt, image])
    payload = clean_json_payload(response.text)
    if isinstance(payload, dict):
        payload = payload.get("transactions") or payload.get("items") or [payload]
    if not isinstance(payload, list):
        raise ValueError("AI 返回的不是交易数组。")
    return [row for row in payload if isinstance(row, dict)]


def prepare_candidates(rows: list[dict[str, Any]], categories: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    defaults = {
        "date": date.today().isoformat(),
        "item": "",
        "category": "其他",
        "type": EXPENSE,
        "amount": None,
        "note": "",
    }
    for column, default in defaults.items():
        if column not in frame.columns:
            frame[column] = default

    frame = frame[["date", "item", "category", "type", "amount", "note"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").fillna(pd.Timestamp(date.today()))
    frame["item"] = frame["item"].fillna("").astype(str).str.strip()
    frame["category"] = frame["category"].fillna("其他").astype(str).str.strip()
    frame.loc[~frame["category"].isin(categories), "category"] = "其他"
    frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, INCOME]), EXPENSE)
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").abs().round(2)
    frame["note"] = frame["note"].fillna("").astype(str).str.strip()

    existing = load_existing_keys()
    duplicates = []
    valid = []
    for row in frame.itertuples(index=False):
        is_valid = bool(str(row.item).strip()) and pd.notna(row.amount) and float(row.amount) > 0
        key = (
            row.date.date().isoformat(),
            str(row.item).strip().casefold(),
            str(row.category).strip().casefold(),
            str(row.type),
            round(float(row.amount), 2),
        ) if is_valid else None
        duplicate = key in existing if key else False
        valid.append(is_valid)
        duplicates.append(duplicate)

    frame.insert(0, "保存", [is_valid and not duplicate for is_valid, duplicate in zip(valid, duplicates)])
    frame["状态"] = ["疑似重复" if duplicate else ("可保存" if is_valid else "资料不完整") for is_valid, duplicate in zip(valid, duplicates)]
    return frame


def create_category(name: str) -> bool:
    cleaned = str(name or "").strip()
    if not cleaned:
        return False
    existing = {category.casefold() for category in load_categories()}
    if cleaned.casefold() in existing:
        return True
    supabase.table("categories").insert({"name": cleaned}).execute()
    load_categories.clear()
    return True


def insert_transactions(frame: pd.DataFrame) -> int:
    selected = frame[frame["保存"]].copy()
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce")
    selected["amount"] = pd.to_numeric(selected["amount"], errors="coerce")
    selected = selected[
        selected["date"].notna()
        & selected["amount"].notna()
        & (selected["amount"] > 0)
        & selected["item"].fillna("").astype(str).str.strip().ne("")
        & selected["type"].isin([EXPENSE, INCOME])
    ]
    if selected.empty:
        return 0

    payload = []
    for row in selected.itertuples(index=False):
        payload.append(
            {
                "date": row.date.date().isoformat(),
                "item": str(row.item).strip(),
                "category": str(row.category).strip() or "其他",
                "type": str(row.type),
                "amount": round(float(row.amount), 2),
                "note": str(row.note or "").strip(),
            }
        )
    supabase.table("transactions").insert(payload).execute()
    load_existing_keys.clear()
    return len(payload)


def image_signature(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()[:18]


st.markdown('<div class="receipt-title">📷 AI 收据识别</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="receipt-subtitle">上传收据或直接拍照，自动拆分项目；检查后才会保存到现有账本。</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="receipt-callout">收据识别与历史数据导入是不同功能。这里一次只处理当前收据，不会覆盖、删除或批量导入旧资料。</div>',
    unsafe_allow_html=True,
)

upload_tab, camera_tab = st.tabs(["上传图片", "直接拍照"])
with upload_tab:
    uploaded = st.file_uploader("上传 JPG、PNG 或 WebP 收据", type=["jpg", "jpeg", "png", "webp"], key="dedicated_receipt_upload")
with camera_tab:
    captured = st.camera_input("拍摄收据", key="dedicated_receipt_camera")

image_file = captured or uploaded
if image_file is None:
    st.info("选择图片或拍照后即可开始识别。")
    st.stop()

image_bytes = image_file.getvalue()
signature = image_signature(image_bytes)
if st.session_state.get(f"{PAGE_KEY}_signature") != signature:
    st.session_state[f"{PAGE_KEY}_signature"] = signature
    st.session_state.pop(f"{PAGE_KEY}_result", None)

preview_col, action_col = st.columns([1, 1.35], gap="large")
with preview_col:
    st.image(image_bytes, caption="待识别收据", use_container_width=True)
with action_col:
    st.markdown('<div class="receipt-step">1. 识别设置</div>', unsafe_allow_html=True)
    instructions = st.text_area(
        "补充说明（可选）",
        placeholder="例如：这是退款单；只识别商品，不要识别会员积分；日期实际是昨天。",
    )
    recognize = st.button("✨ 使用 Gemini 3.6 Flash 识别", type="primary", use_container_width=True)
    clear = st.button("清除识别结果", use_container_width=True)

if clear:
    st.session_state.pop(f"{PAGE_KEY}_result", None)
    st.rerun()

if recognize:
    try:
        with st.spinner("正在读取收据并拆分项目…"):
            rows = analyze_receipt(Image.open(image_file), load_categories(), instructions.strip())
            st.session_state[f"{PAGE_KEY}_result"] = rows
        st.rerun()
    except Exception as exc:
        st.error(f"收据识别失败：{exc}")

if f"{PAGE_KEY}_result" not in st.session_state:
    st.stop()

rows = st.session_state[f"{PAGE_KEY}_result"]
if not rows:
    st.warning("AI 没有识别到可用交易，请换一张更清晰的图片。")
    st.stop()

categories = load_categories()
st.markdown('<div class="receipt-step">2. 检查并修改</div>', unsafe_allow_html=True)
st.caption("请核对项目、类别、日期和金额。只有勾选“保存”的行会写入账本。")

new_category_col, refresh_col = st.columns([1.4, 1])
new_category = new_category_col.text_input("需要新类别时先在这里建立", placeholder="例如：宠物")
if refresh_col.button("＋ 建立类别", use_container_width=True):
    try:
        if create_category(new_category):
            st.success(f"已建立类别：{new_category.strip()}")
            st.rerun()
        else:
            st.warning("请输入类别名称。")
    except Exception as exc:
        st.error(f"建立类别失败：{exc}")

candidates = prepare_candidates(rows, load_categories())
edited = st.data_editor(
    candidates,
    hide_index=True,
    use_container_width=True,
    height=min(620, 84 + max(len(candidates), 4) * 42),
    num_rows="dynamic",
    disabled=["状态"],
    column_config={
        "保存": st.column_config.CheckboxColumn("保存"),
        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
        "item": st.column_config.TextColumn("项目／商家", required=True, width="large"),
        "category": st.column_config.SelectboxColumn("类别", options=load_categories(), required=True),
        "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, INCOME], required=True),
        "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
        "note": st.column_config.TextColumn("备注", width="large"),
        "状态": st.column_config.TextColumn("状态"),
    },
    key=f"{PAGE_KEY}_editor_{signature}",
)

selected_count = int(edited["保存"].sum())
expense_total = float(edited.loc[edited["保存"] & edited["type"].eq(EXPENSE), "amount"].sum())
income_total = float(edited.loc[edited["保存"] & edited["type"].eq(INCOME), "amount"].sum())
metric1, metric2, metric3 = st.columns(3)
metric1.metric("准备保存", f"{selected_count} 笔")
metric2.metric("支出", f"RM {expense_total:,.2f}")
metric3.metric("收入", f"RM {income_total:,.2f}")

st.markdown('<div class="receipt-step">3. 确认保存</div>', unsafe_allow_html=True)
confirm = st.checkbox(f"我已核对，并确认将 {selected_count} 笔交易新增到现有账本。", disabled=selected_count == 0)
if st.button("保存选中项目", type="primary", use_container_width=True, disabled=not confirm or selected_count == 0):
    try:
        with st.spinner("正在保存到 Supabase…"):
            saved = insert_transactions(edited)
        if saved:
            st.success(f"成功保存 {saved} 笔交易。")
            st.session_state.pop(f"{PAGE_KEY}_result", None)
            st.balloons()
        else:
            st.warning("没有符合条件的记录可保存。")
    except Exception as exc:
        st.error(f"保存失败：{exc}")
