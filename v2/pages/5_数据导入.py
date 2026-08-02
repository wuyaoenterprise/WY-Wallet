"""Flexible import wizard for WY Wallet V2.

This Streamlit page imports transaction exports from other finance apps without
changing the existing Supabase schema. Users map source columns to WY Wallet's
fields, preview normalized rows, and import only confirmed records.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date
from typing import Any

import pandas as pd
import streamlit as st
from supabase import create_client


EXPENSE = "Expense"
INCOME = "Income"
SKIP = "— 不使用 —"
TARGET_FIELDS = ["date", "item", "category", "type", "amount", "expense", "income", "note", "currency"]
FIELD_LABELS = {
    "date": "日期",
    "item": "项目／商家",
    "category": "类别",
    "type": "类型",
    "amount": "金额",
    "expense": "支出金额",
    "income": "收入金额",
    "note": "备注",
    "currency": "币种",
}
ALIASES = {
    "date": ["date", "transactiondate", "datetime", "time", "日期", "交易日期", "时间", "日期时间"],
    "item": ["item", "description", "payee", "merchant", "name", "title", "项目", "描述", "商家", "收款方", "交易说明"],
    "category": ["category", "group", "分类", "类别", "科目"],
    "type": ["type", "transactiontype", "flow", "收支类型", "类型", "方向"],
    "amount": ["amount", "value", "total", "transactionamount", "金额", "总额", "交易金额"],
    "expense": ["expense", "debit", "withdrawal", "outflow", "支出", "借方", "提款"],
    "income": ["income", "credit", "deposit", "inflow", "收入", "贷方", "存入"],
    "note": ["note", "notes", "memo", "comment", "remarks", "备注", "附注", "说明"],
    "currency": ["currency", "currencycode", "币种", "货币"],
}
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]

st.set_page_config(page_title="数据导入 · WY Wallet V2", page_icon="📥", layout="wide")
st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 1280px; padding-top: 1.15rem; padding-bottom: 3rem;
    }
    .import-title {font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin-bottom:.15rem}
    .import-subtitle {opacity:.72;margin-bottom:1.1rem}
    .import-step {font-size:1.05rem;font-weight:750;margin:.2rem 0 .65rem}
    .import-callout {border-left:3px solid #5b8ff9;padding:.7rem .9rem;background:rgba(91,143,249,.08);border-radius:0 10px 10px 0;margin:.5rem 0}
    div[data-testid="stMetric"] {border:1px solid rgba(128,128,128,.24);border-radius:14px;padding:.8rem 1rem;background:rgba(127,127,127,.035)}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
except Exception as exc:
    st.error(f"数据库配置加载失败：{exc}")
    st.stop()


def normalized_name(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()


def find_alias(columns: list[str], field: str) -> str | None:
    normalized_columns = {normalized_name(column): column for column in columns}
    for alias in ALIASES[field]:
        alias_key = normalized_name(alias)
        if alias_key in normalized_columns:
            return normalized_columns[alias_key]
    for alias in ALIASES[field]:
        alias_key = normalized_name(alias)
        for key, column in normalized_columns.items():
            if alias_key and (alias_key in key or key in alias_key):
                return column
    return None


def read_csv_flexible(uploaded_file) -> tuple[pd.DataFrame, str]:
    raw = uploaded_file.getvalue()
    errors = []
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "big5", "latin1"]:
        try:
            frame = pd.read_csv(io.BytesIO(raw), encoding=encoding, sep=None, engine="python")
            return frame, encoding
        except Exception as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("无法读取 CSV。尝试过的编码：" + " | ".join(errors[-3:]))


def extract_json_records(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ["transactions", "records", "data", "items", "result"]:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
        if all(not isinstance(item, (list, dict)) for item in value.values()):
            return [value]
    raise ValueError("JSON 中找不到交易数组。支持顶层数组，或 transactions / records / data / items。")


def read_source_file(uploaded_file, sheet_name: str | None = None) -> tuple[pd.DataFrame, str]:
    suffix = uploaded_file.name.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        return read_csv_flexible(uploaded_file)
    if suffix in {"xlsx", "xls"}:
        frame = pd.read_excel(uploaded_file, sheet_name=sheet_name or 0)
        return frame, f"Excel · {sheet_name or '第一个工作表'}"
    if suffix == "json":
        payload = json.loads(uploaded_file.getvalue().decode("utf-8-sig"))
        return pd.DataFrame(extract_json_records(payload)), "JSON"
    raise ValueError("只支持 CSV、XLSX、XLS 和 JSON。")


def clean_amount_series(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip()
    negative_parentheses = text.str.match(r"^\(.*\)$")
    cleaned = (
        text.str.replace(r"[\s,]", "", regex=True)
        .str.replace(r"(?i)rm|myr|usd|sgd|cny|rmb|eur|gbp", "", regex=True)
        .str.replace(r"[^0-9.\-+]", "", regex=True)
        .str.replace(r"^(\+)", "", regex=True)
    )
    result = pd.to_numeric(cleaned, errors="coerce")
    result.loc[negative_parentheses & result.notna()] = -result.loc[negative_parentheses & result.notna()].abs()
    return result


def parse_dates(series: pd.Series, mode: str, day_first: bool) -> pd.Series:
    if mode == "Unix 秒":
        return pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit="s", errors="coerce")
    if mode == "Unix 毫秒":
        return pd.to_datetime(pd.to_numeric(series, errors="coerce"), unit="ms", errors="coerce")
    formats = {
        "YYYY-MM-DD": "%Y-%m-%d",
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "DD-MM-YYYY": "%d-%m-%Y",
        "YYYY/MM/DD": "%Y/%m/%d",
    }
    if mode in formats:
        return pd.to_datetime(series, format=formats[mode], errors="coerce")
    return pd.to_datetime(series, errors="coerce", dayfirst=day_first)


def keyword_set(text: str) -> set[str]:
    return {normalized_name(item) for item in re.split(r"[,，;；\n]+", text) if str(item).strip()}


def classify_type(value: Any, income_labels: set[str], expense_labels: set[str]) -> str | None:
    key = normalized_name(value)
    if key in income_labels or any(label and label in key for label in income_labels):
        return INCOME
    if key in expense_labels or any(label and label in key for label in expense_labels):
        return EXPENSE
    return None


def fingerprint(frame: pd.DataFrame) -> str:
    sample = frame.head(100).astype(str).to_csv(index=False).encode("utf-8", errors="ignore")
    return hashlib.sha256(sample).hexdigest()[:16]


@st.cache_data(ttl=300, show_spinner=False)
def load_existing_transactions() -> pd.DataFrame:
    try:
        response = supabase.table("transactions").select("date,item,category,type,amount,note").execute()
        frame = pd.DataFrame(response.data)
        if frame.empty:
            return pd.DataFrame(columns=["date", "item", "category", "type", "amount", "note"])
        for column, default in {"item": "未知", "category": "其他", "type": EXPENSE, "amount": 0.0, "note": ""}.items():
            if column not in frame.columns:
                frame[column] = default
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0).round(2)
        for column in ["item", "category", "type", "note"]:
            frame[column] = frame[column].fillna("").astype(str).str.strip()
        return frame
    except Exception as exc:
        st.warning(f"无法载入现有交易进行重复检查：{exc}")
        return pd.DataFrame(columns=["date", "item", "category", "type", "amount", "note"])


@st.cache_data(ttl=1800, show_spinner=False)
def load_existing_categories() -> list[str]:
    try:
        rows = supabase.table("categories").select("name").execute().data
        values = [str(row.get("name", "")).strip() for row in rows]
        return sorted({value for value in values if value}) or DEFAULT_CATEGORIES.copy()
    except Exception:
        return DEFAULT_CATEGORIES.copy()


def build_existing_keys(frame: pd.DataFrame) -> set[tuple]:
    if frame.empty:
        return set()
    return set(
        zip(
            frame["date"].astype(str),
            frame["item"].astype(str).str.casefold(),
            frame["category"].astype(str).str.casefold(),
            frame["type"].astype(str),
            frame["amount"].round(2),
            frame["note"].astype(str).str.casefold(),
        )
    )


def normalize_source(
    source: pd.DataFrame,
    mapping: dict[str, str | None],
    amount_mode: str,
    type_strategy: str,
    sign_strategy: str,
    date_mode: str,
    day_first: bool,
    income_labels: set[str],
    expense_labels: set[str],
    default_category: str,
    currency_filter: str,
    append_source: bool,
    source_name: str,
) -> pd.DataFrame:
    result = pd.DataFrame(index=source.index)
    result["_source_row"] = source.index + 2

    date_column = mapping.get("date")
    result["date"] = parse_dates(source[date_column], date_mode, day_first) if date_column else pd.NaT

    item_column = mapping.get("item")
    result["item"] = source[item_column].fillna("").astype(str).str.strip() if item_column else ""

    category_column = mapping.get("category")
    if category_column:
        result["category"] = source[category_column].fillna("").astype(str).str.strip().replace("", default_category)
    else:
        result["category"] = default_category

    note_column = mapping.get("note")
    result["note"] = source[note_column].fillna("").astype(str).str.strip() if note_column else ""
    if append_source:
        suffix = f"[导入自 {source_name}]"
        result["note"] = result["note"].apply(lambda value: f"{value} {suffix}".strip())

    currency_column = mapping.get("currency")
    if currency_column and currency_filter.strip():
        allowed = keyword_set(currency_filter)
        currency_keys = source[currency_column].fillna("").astype(str).map(normalized_name)
        result["_currency_ok"] = currency_keys.map(lambda value: value in allowed)
    else:
        result["_currency_ok"] = True

    if amount_mode == "支出与收入分开两列":
        expense_column = mapping.get("expense")
        income_column = mapping.get("income")
        expense_values = clean_amount_series(source[expense_column]) if expense_column else pd.Series(0.0, index=source.index)
        income_values = clean_amount_series(source[income_column]) if income_column else pd.Series(0.0, index=source.index)
        has_income = income_values.fillna(0).abs() > 0
        has_expense = expense_values.fillna(0).abs() > 0
        result["type"] = pd.Series(pd.NA, index=source.index, dtype="object")
        result.loc[has_expense & ~has_income, "type"] = EXPENSE
        result.loc[has_income & ~has_expense, "type"] = INCOME
        result.loc[has_income & has_expense, "type"] = pd.NA
        result["amount"] = expense_values.where(has_expense, income_values).abs()
    else:
        amount_column = mapping.get("amount")
        raw_amount = clean_amount_series(source[amount_column]) if amount_column else pd.Series(pd.NA, index=source.index)
        result["amount"] = raw_amount.abs()
        if type_strategy == "来源类型列":
            type_column = mapping.get("type")
            result["type"] = source[type_column].map(lambda value: classify_type(value, income_labels, expense_labels)) if type_column else pd.NA
        elif type_strategy == "金额正负自动判断":
            if sign_strategy == "负数是支出，正数是收入":
                result["type"] = raw_amount.map(lambda value: EXPENSE if pd.notna(value) and value < 0 else (INCOME if pd.notna(value) and value > 0 else None))
            else:
                result["type"] = raw_amount.map(lambda value: INCOME if pd.notna(value) and value < 0 else (EXPENSE if pd.notna(value) and value > 0 else None))
        elif type_strategy == "全部作为收入":
            result["type"] = INCOME
        else:
            result["type"] = EXPENSE

    result["amount"] = pd.to_numeric(result["amount"], errors="coerce").round(2)
    result["_error"] = ""
    result.loc[result["date"].isna(), "_error"] += "日期无法识别；"
    result.loc[result["item"].astype(str).str.strip().eq(""), "_error"] += "项目为空；"
    result.loc[result["amount"].isna() | (result["amount"] <= 0), "_error"] += "金额无效；"
    result.loc[~result["type"].isin([EXPENSE, INCOME]), "_error"] += "类型无法判断；"
    result.loc[~result["_currency_ok"], "_error"] += "币种被过滤；"
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    return result


def prepare_preview(normalized: pd.DataFrame, existing_keys: set[tuple]) -> pd.DataFrame:
    preview = normalized.copy()
    preview["_duplicate"] = False
    valid_mask = preview["_error"].eq("")
    for index, row in preview.loc[valid_mask].iterrows():
        key = (
            row["date"].date().isoformat(),
            str(row["item"]).strip().casefold(),
            str(row["category"]).strip().casefold(),
            str(row["type"]),
            round(float(row["amount"]), 2),
            str(row["note"]).strip().casefold(),
        )
        preview.at[index, "_duplicate"] = key in existing_keys
    preview["状态"] = "可导入"
    preview.loc[preview["_duplicate"], "状态"] = "疑似重复"
    preview.loc[preview["_error"].ne(""), "状态"] = "无法导入"
    return preview


def insert_rows(rows: list[dict], chunk_size: int = 500) -> None:
    for start in range(0, len(rows), chunk_size):
        supabase.table("transactions").insert(rows[start : start + chunk_size]).execute()


def create_missing_categories(values: list[str]) -> None:
    existing = {value.casefold() for value in load_existing_categories()}
    missing = sorted({value.strip() for value in values if value.strip() and value.casefold() not in existing})
    if missing:
        supabase.table("categories").insert([{"name": value} for value in missing]).execute()


st.markdown('<div class="import-title">📥 外部数据导入</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="import-subtitle">支持其他记账应用和银行导出的 CSV、Excel、JSON。不会覆盖或删除现有数据。</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="import-callout">流程：上传文件 → 选择工作表 → 对应栏位 → 预览与重复检查 → 确认导入。所有转换先在浏览器会话中预览，确认后才写入 Supabase。</div>',
    unsafe_allow_html=True,
)

uploaded_file = st.file_uploader("上传导出文件", type=["csv", "xlsx", "xls", "json"], accept_multiple_files=False)
if uploaded_file is None:
    st.info("上传文件后会自动识别常见列名，例如 Date、Description、Category、Amount、Debit、Credit、备注等。")
    st.stop()

sheet_name = None
if uploaded_file.name.lower().endswith((".xlsx", ".xls")):
    try:
        excel = pd.ExcelFile(uploaded_file)
        sheet_name = st.selectbox("选择工作表", excel.sheet_names)
        uploaded_file.seek(0)
    except Exception as exc:
        st.error(f"无法读取 Excel 工作表：{exc}")
        st.stop()

try:
    source_frame, detected_format = read_source_file(uploaded_file, sheet_name)
except Exception as exc:
    st.error(f"文件读取失败：{exc}")
    st.stop()

source_frame.columns = [str(column).strip() or f"未命名栏位_{index + 1}" for index, column in enumerate(source_frame.columns)]
source_frame = source_frame.dropna(how="all").reset_index(drop=True)
if source_frame.empty:
    st.warning("文件中没有可读取的数据行。")
    st.stop()

file_signature = fingerprint(source_frame)
meta1, meta2, meta3, meta4 = st.columns(4)
meta1.metric("数据行", f"{len(source_frame):,}")
meta2.metric("栏位", len(source_frame.columns))
meta3.metric("读取方式", detected_format)
meta4.metric("文件标识", file_signature)

with st.expander("查看原始数据", expanded=False):
    st.dataframe(source_frame.head(100), hide_index=True, use_container_width=True, height=360)
    st.caption("仅显示前 100 行；实际导入会处理完整文件。")

columns = source_frame.columns.tolist()
auto = {field: find_alias(columns, field) for field in TARGET_FIELDS}

st.markdown('<div class="import-step">1. 对应来源栏位</div>', unsafe_allow_html=True)
source_name = st.text_input("来源名称（可选）", value=uploaded_file.name.rsplit(".", 1)[0], help="可附加在备注中，方便日后追踪。")
amount_mode = st.radio("金额结构", ["单一金额列", "支出与收入分开两列"], horizontal=True)
options = [SKIP] + columns

mapping: dict[str, str | None] = {}
row1 = st.columns(4)
for container, field in zip(row1, ["date", "item", "category", "note"]):
    default_value = auto[field] if auto[field] in columns else SKIP
    selected = container.selectbox(FIELD_LABELS[field], options, index=options.index(default_value), key=f"map_{field}_{file_signature}")
    mapping[field] = None if selected == SKIP else selected

if amount_mode == "单一金额列":
    row2 = st.columns(3)
    for container, field in zip(row2, ["amount", "type", "currency"]):
        default_value = auto[field] if auto[field] in columns else SKIP
        selected = container.selectbox(FIELD_LABELS[field], options, index=options.index(default_value), key=f"map_{field}_{file_signature}")
        mapping[field] = None if selected == SKIP else selected
    mapping["expense"] = None
    mapping["income"] = None
else:
    row2 = st.columns(3)
    for container, field in zip(row2, ["expense", "income", "currency"]):
        default_value = auto[field] if auto[field] in columns else SKIP
        selected = container.selectbox(FIELD_LABELS[field], options, index=options.index(default_value), key=f"map_{field}_{file_signature}")
        mapping[field] = None if selected == SKIP else selected
    mapping["amount"] = None
    mapping["type"] = None

if not mapping.get("date") or not mapping.get("item"):
    st.warning("日期和项目／商家是必填对应栏位。")

st.markdown('<div class="import-step">2. 转换规则</div>', unsafe_allow_html=True)
rule1, rule2, rule3 = st.columns(3)
date_mode = rule1.selectbox("日期格式", ["自动识别", "YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY", "DD-MM-YYYY", "YYYY/MM/DD", "Unix 秒", "Unix 毫秒"])
day_first = rule1.checkbox("自动识别时优先日/月/年", value=True)
current_categories = load_existing_categories()
default_category = rule2.selectbox("缺少类别时使用", current_categories, index=current_categories.index("其他") if "其他" in current_categories else 0)
append_source = rule2.checkbox("在备注附加来源", value=False)
currency_filter = rule3.text_input("只导入这些币种（可选）", value="MYR, RM" if mapping.get("currency") else "", help="只有对应了币种栏位时才生效；用逗号分隔。")
create_categories = rule3.checkbox("自动建立文件中的新类别", value=True)

income_labels = keyword_set("Income,收入,Credit,Deposit,Inflow,进账,入账")
expense_labels = keyword_set("Expense,支出,Debit,Withdrawal,Outflow,消费,付款")
sign_strategy = "负数是支出，正数是收入"
type_strategy = "全部作为支出"
if amount_mode == "单一金额列":
    type_strategy = st.radio("如何判断收入／支出", ["来源类型列", "金额正负自动判断", "全部作为支出", "全部作为收入"], horizontal=True)
    if type_strategy == "来源类型列":
        label1, label2 = st.columns(2)
        income_text = label1.text_input("代表收入的文字", "Income, 收入, Credit, Deposit, Inflow, 进账, 入账")
        expense_text = label2.text_input("代表支出的文字", "Expense, 支出, Debit, Withdrawal, Outflow, 消费, 付款")
        income_labels = keyword_set(income_text)
        expense_labels = keyword_set(expense_text)
    elif type_strategy == "金额正负自动判断":
        sign_strategy = st.radio("正负号规则", ["负数是支出，正数是收入", "正数是支出，负数是收入"], horizontal=True)

required_ready = bool(mapping.get("date") and mapping.get("item"))
if amount_mode == "单一金额列":
    required_ready = required_ready and bool(mapping.get("amount"))
    if type_strategy == "来源类型列":
        required_ready = required_ready and bool(mapping.get("type"))
else:
    required_ready = required_ready and bool(mapping.get("expense") or mapping.get("income"))

st.markdown('<div class="import-step">3. 预览与检查</div>', unsafe_allow_html=True)
if not required_ready:
    st.info("完成必填栏位对应后才能生成预览。")
    st.stop()

try:
    normalized = normalize_source(
        source_frame,
        mapping,
        amount_mode,
        type_strategy,
        sign_strategy,
        date_mode,
        day_first,
        income_labels,
        expense_labels,
        default_category,
        currency_filter,
        append_source,
        source_name,
    )
    existing_keys = build_existing_keys(load_existing_transactions())
    preview = prepare_preview(normalized, existing_keys)
except Exception as exc:
    st.error(f"转换失败：{exc}")
    st.stop()

valid_count = int((preview["状态"] == "可导入").sum())
duplicate_count = int((preview["状态"] == "疑似重复").sum())
invalid_count = int((preview["状态"] == "无法导入").sum())
expense_total = float(preview.loc[(preview["状态"] != "无法导入") & (preview["type"] == EXPENSE), "amount"].sum())
income_total = float(preview.loc[(preview["状态"] != "无法导入") & (preview["type"] == INCOME), "amount"].sum())

p1, p2, p3, p4, p5 = st.columns(5)
p1.metric("可导入", f"{valid_count:,} 笔")
p2.metric("疑似重复", f"{duplicate_count:,} 笔")
p3.metric("无法导入", f"{invalid_count:,} 笔")
p4.metric("支出合计", f"RM {expense_total:,.2f}")
p5.metric("收入合计", f"RM {income_total:,.2f}")

preview_display = preview.copy()
preview_display["日期"] = preview_display["date"].dt.strftime("%Y-%m-%d")
preview_display["项目"] = preview_display["item"]
preview_display["类别"] = preview_display["category"]
preview_display["类型"] = preview_display["type"].map({EXPENSE: "支出", INCOME: "收入"})
preview_display["金额"] = preview_display["amount"]
preview_display["备注"] = preview_display["note"]
preview_display["问题"] = preview_display["_error"].str.rstrip("；")
st.dataframe(
    preview_display[["状态", "_source_row", "日期", "项目", "类别", "类型", "金额", "备注", "问题"]].rename(columns={"_source_row": "原文件行"}),
    hide_index=True,
    use_container_width=True,
    height=480,
    column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")},
)

invalid_export = preview.loc[preview["状态"] == "无法导入"].copy()
if not invalid_export.empty:
    st.download_button(
        "下载无法导入的行",
        invalid_export.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"WY_Wallet_import_errors_{date.today()}.csv",
        mime="text/csv",
    )

st.markdown('<div class="import-step">4. 确认导入</div>', unsafe_allow_html=True)
include_duplicates = st.checkbox("仍然导入疑似重复记录", value=False)
import_candidates = preview[preview["状态"].isin(["可导入"] + (["疑似重复"] if include_duplicates else []))].copy()
st.caption(f"本次准备写入 {len(import_candidates):,} 笔。导入只会新增，不会覆盖或删除任何现有交易。")
confirm = st.checkbox("我已检查预览，并确认写入现有 Supabase 数据库")

if st.button("开始导入", type="primary", use_container_width=True, disabled=not confirm or import_candidates.empty):
    payload = []
    for _, row in import_candidates.iterrows():
        payload.append(
            {
                "date": row["date"].date().isoformat(),
                "item": str(row["item"]).strip() or "未知",
                "category": str(row["category"]).strip() or default_category,
                "type": str(row["type"]),
                "amount": round(float(row["amount"]), 2),
                "note": str(row["note"]).strip(),
            }
        )
    try:
        with st.status("正在导入…", expanded=True) as status:
            if create_categories:
                st.write("检查并建立新类别…")
                create_missing_categories([row["category"] for row in payload])
            st.write(f"分批写入 {len(payload):,} 笔交易…")
            insert_rows(payload)
            load_existing_transactions.clear()
            load_existing_categories.clear()
            status.update(label=f"成功导入 {len(payload):,} 笔交易", state="complete", expanded=True)
        st.success("导入完成。回到“交易记录”页面即可查看，默认会按日期排序。")
    except Exception as exc:
        st.error(f"导入失败：{exc}")
        st.warning("部分批次可能已经写入。再次导入前请重新上传文件并检查“疑似重复”。")
