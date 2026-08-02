"""AI-assisted import wizard for messy finance documents in WY Wallet V2.

The page uses Gemini 3.6 Flash to extract candidate transactions from messy
spreadsheets, CSV/JSON/text files, PDFs, images, and DOCX documents. AI output
is always reviewed and edited before any row is inserted into Supabase.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from supabase import create_client


MODEL_ID = "gemini-3.6-flash"
EXPENSE = "Expense"
INCOME = "Income"
UNKNOWN = "Unknown"
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
SUPPORTED_EXTENSIONS = ["csv", "xlsx", "xls", "json", "txt", "md", "pdf", "png", "jpg", "jpeg", "webp", "docx"]
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
TEXT_CHUNK_CHARS = 90_000


class ExtractedTransaction(BaseModel):
    date: str | None = Field(default=None, description="Transaction date in YYYY-MM-DD, or null when not present.")
    item: str = Field(default="", description="Short merchant, payee, or transaction item name.")
    category: str = Field(default="其他", description="Expense or income category.")
    type: Literal["Expense", "Income", "Unknown"] = Field(default="Unknown", description="Cash-flow direction.")
    amount: float | None = Field(default=None, description="Positive absolute amount, without currency symbols.")
    note: str = Field(default="", description="Useful original description or memo, excluding invented information.")
    currency: str | None = Field(default=None, description="Currency code or symbol when visible, otherwise null.")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence from 0 to 1 for the complete row.")
    source_reference: str = Field(default="", description="Page, row, line, sheet, or visual region used as evidence.")
    review_reason: str = Field(default="", description="Why human review is needed; blank when no obvious issue.")


class ExtractionResult(BaseModel):
    source_summary: str = Field(default="", description="Brief description of the source and its layout.")
    warnings: list[str] = Field(default_factory=list, description="Document-level ambiguities or extraction limitations.")
    transactions: list[ExtractedTransaction] = Field(default_factory=list)


st.set_page_config(page_title="AI 整理导入 · WY Wallet V2", page_icon="✨", layout="wide")
st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 1320px; padding-top: 1.1rem; padding-bottom: 3rem;
    }
    .ai-title {font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin-bottom:.15rem}
    .ai-subtitle {opacity:.72;margin-bottom:1rem}
    .ai-step {font-size:1.06rem;font-weight:760;margin:.35rem 0 .65rem}
    .ai-callout {border-left:3px solid #5b8ff9;padding:.72rem .95rem;background:rgba(91,143,249,.08);border-radius:0 10px 10px 0;margin:.55rem 0}
    .ai-warning {border-left:3px solid #f6bd16;padding:.72rem .95rem;background:rgba(246,189,22,.08);border-radius:0 10px 10px 0;margin:.55rem 0}
    div[data-testid="stMetric"] {border:1px solid rgba(128,128,128,.24);border-radius:14px;padding:.8rem 1rem;background:rgba(127,127,127,.035)}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    gemini = genai.Client(api_key=st.secrets["GOOGLE_API_KEY"])
except Exception as exc:
    st.error(f"配置加载失败：{exc}")
    st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def load_existing_transactions() -> pd.DataFrame:
    columns = ["date", "item", "category", "type", "amount"]
    try:
        rows = supabase.table("transactions").select("date,item,category,type,amount").execute().data
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=columns)
        for column, default in {"item": "", "category": "其他", "type": EXPENSE, "amount": 0.0}.items():
            if column not in frame.columns:
                frame[column] = default
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0).round(2)
        for column in ["item", "category", "type"]:
            frame[column] = frame[column].fillna("").astype(str).str.strip()
        return frame[columns]
    except Exception as exc:
        st.warning(f"无法读取现有交易进行重复检查：{exc}")
        return pd.DataFrame(columns=columns)


@st.cache_data(ttl=1800, show_spinner=False)
def load_categories() -> list[str]:
    try:
        rows = supabase.table("categories").select("name").execute().data
        values = sorted({str(row.get("name", "")).strip() for row in rows if str(row.get("name", "")).strip()})
        return values or DEFAULT_CATEGORIES.copy()
    except Exception:
        return DEFAULT_CATEGORIES.copy()


def clear_cache() -> None:
    load_existing_transactions.clear()
    load_categories.clear()


def file_signature(files) -> str:
    digest = hashlib.sha256()
    for uploaded in files:
        raw = uploaded.getvalue()
        digest.update(uploaded.name.encode("utf-8", errors="ignore"))
        digest.update(str(len(raw)).encode())
        digest.update(raw[:200_000])
    return digest.hexdigest()[:20]


def decode_text(raw: bytes) -> str:
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "big5", "latin1"]:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_csv_flexible(raw: bytes) -> pd.DataFrame:
    errors = []
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "big5", "latin1"]:
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=encoding, sep=None, engine="python")
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("CSV 无法读取：" + (errors[-1] if errors else "未知错误"))


def extract_docx_text(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        xml_data = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_data)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        pieces = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        text = "".join(pieces).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def dataframe_to_tasks(frame: pd.DataFrame, source_name: str, section_name: str, rows_per_call: int) -> list[dict[str, Any]]:
    frame = frame.dropna(how="all").reset_index(drop=True)
    frame.columns = [str(column).strip() or f"column_{index + 1}" for index, column in enumerate(frame.columns)]
    tasks = []
    for start in range(0, len(frame), rows_per_call):
        end = min(start + rows_per_call, len(frame))
        chunk = frame.iloc[start:end].copy()
        content = chunk.to_csv(index=True, index_label="source_row")
        tasks.append({
            "kind": "text",
            "source_file": source_name,
            "source_section": f"{section_name} · rows {start + 2}-{end + 1}",
            "content": content,
        })
    return tasks


def text_to_tasks(text: str, source_name: str, section_name: str = "text") -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    tasks = []
    for start in range(0, len(text), TEXT_CHUNK_CHARS):
        end = min(start + TEXT_CHUNK_CHARS, len(text))
        tasks.append({
            "kind": "text",
            "source_file": source_name,
            "source_section": f"{section_name} · characters {start + 1}-{end}",
            "content": text[start:end],
        })
    return tasks


def build_tasks(uploaded_files, rows_per_call: int) -> tuple[list[dict[str, Any]], list[str]]:
    tasks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for uploaded in uploaded_files:
        raw = uploaded.getvalue()
        suffix = Path(uploaded.name).suffix.lower()
        if len(raw) > MAX_FILE_BYTES:
            warnings.append(f"{uploaded.name} 超过 20 MB，已跳过。")
            continue
        try:
            if suffix == ".csv":
                tasks.extend(dataframe_to_tasks(read_csv_flexible(raw), uploaded.name, "CSV", rows_per_call))
            elif suffix in {".xlsx", ".xls"}:
                sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None)
                for sheet_name, frame in sheets.items():
                    tasks.extend(dataframe_to_tasks(frame, uploaded.name, f"sheet {sheet_name}", rows_per_call))
            elif suffix == ".json":
                payload = json.loads(decode_text(raw))
                if isinstance(payload, list):
                    tasks.extend(dataframe_to_tasks(pd.json_normalize(payload), uploaded.name, "JSON array", rows_per_call))
                elif isinstance(payload, dict):
                    list_value = next((value for value in payload.values() if isinstance(value, list)), None)
                    if list_value is not None:
                        tasks.extend(dataframe_to_tasks(pd.json_normalize(list_value), uploaded.name, "JSON records", rows_per_call))
                    else:
                        tasks.extend(text_to_tasks(json.dumps(payload, ensure_ascii=False, indent=2), uploaded.name, "JSON object"))
                else:
                    tasks.extend(text_to_tasks(str(payload), uploaded.name, "JSON value"))
            elif suffix in {".txt", ".md"}:
                tasks.extend(text_to_tasks(decode_text(raw), uploaded.name))
            elif suffix == ".docx":
                tasks.extend(text_to_tasks(extract_docx_text(raw), uploaded.name, "DOCX text"))
            elif suffix == ".pdf":
                tasks.append({"kind": "binary", "source_file": uploaded.name, "source_section": "PDF", "raw": raw, "mime_type": "application/pdf"})
            elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                mime_type = mimetypes.guess_type(uploaded.name)[0] or "image/jpeg"
                tasks.append({"kind": "binary", "source_file": uploaded.name, "source_section": "image", "raw": raw, "mime_type": mime_type})
            else:
                warnings.append(f"{uploaded.name} 的格式不受支持。")
        except Exception as exc:
            warnings.append(f"{uploaded.name} 读取失败：{exc}")
    return tasks, warnings


def extraction_prompt(
    source_file: str,
    source_section: str,
    categories: list[str],
    allow_new_categories: bool,
    day_first: bool,
    default_currency: str,
    extra_instructions: str,
) -> str:
    category_rule = (
        f"优先使用这些现有类别：{categories}。只有明显不适合时才创建一个简短的新类别。"
        if allow_new_categories
        else f"category 必须严格从这些类别选择：{categories}；无法判断时使用“其他”。"
    )
    locale_rule = "模糊数字日期优先按 日/月/年 解释。" if day_first else "模糊数字日期优先按 月/日/年 解释。"
    return f"""
你是财务数据整理助手。请从用户提供的混乱资料中提取真实的收入与支出交易，输出必须符合给定 JSON schema。

来源文件：{source_file}
来源区域：{source_section}
默认币种：{default_currency or '未知'}
日期规则：{locale_rule}
类别规则：{category_rule}
用户补充说明：{extra_instructions or '无'}

严格规则：
1. 只提取资料中确实存在的交易，不要补写、推测或制造交易。
2. 不要把余额、结余、期初余额、期末余额、小计、总计、税额汇总、预算或统计数字当成交易。
3. 同一笔交易跨行显示时应合并；重复出现的页眉、页脚和重复表格不要重复提取。
4. date 统一输出 YYYY-MM-DD。资料没有日期或日期无法可靠判断时，date=null，并说明 review_reason。
5. amount 始终输出正的绝对金额，不含货币符号。支出/扣款/付款/借方为 Expense；收入/退款/存入/贷方为 Income。
6. item 使用简短清楚的商家、收款方或项目名称。原始长描述可放进 note。
7. currency 只有资料明确出现时才填写；否则使用默认币种 {default_currency or 'null'}。
8. confidence 代表整行准确度。任何日期、金额、类型或行归属不确定时，必须降低 confidence 并写 review_reason。
9. source_reference 写明页码、行号、工作表、段落或可定位区域。
10. 如果本区域没有交易，transactions 返回空数组，不要硬凑结果。
""".strip()


def call_gemini(task: dict[str, Any], prompt: str) -> ExtractionResult:
    if task["kind"] == "binary":
        contents: Any = [
            types.Part.from_bytes(data=task["raw"], mime_type=task["mime_type"]),
            prompt,
        ]
    else:
        contents = f"{prompt}\n\n以下是来源资料：\n---\n{task['content']}\n---"

    response = gemini.models.generate_content(
        model=MODEL_ID,
        contents=contents,
        config={
            "response_format": {
                "text": {
                    "mime_type": "application/json",
                    "schema": ExtractionResult.model_json_schema(),
                }
            }
        },
    )
    return ExtractionResult.model_validate_json(response.text)


def run_extraction(
    tasks: list[dict[str, Any]],
    categories: list[str],
    allow_new_categories: bool,
    day_first: bool,
    default_currency: str,
    extra_instructions: str,
    max_calls: int,
) -> tuple[pd.DataFrame, list[str], list[dict[str, str]]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    summaries: list[dict[str, str]] = []
    selected_tasks = tasks[:max_calls]
    progress = st.progress(0, text="准备调用 Gemini 3.6 Flash…")

    for index, task in enumerate(selected_tasks, start=1):
        progress.progress((index - 1) / max(len(selected_tasks), 1), text=f"正在整理 {index}/{len(selected_tasks)}：{task['source_file']} · {task['source_section']}")
        prompt = extraction_prompt(
            task["source_file"], task["source_section"], categories,
            allow_new_categories, day_first, default_currency, extra_instructions,
        )
        try:
            result = call_gemini(task, prompt)
        except Exception as first_error:
            time.sleep(1.2)
            try:
                result = call_gemini(task, prompt)
            except Exception as second_error:
                warnings.append(f"{task['source_file']} · {task['source_section']} 处理失败：{second_error or first_error}")
                continue

        summaries.append({
            "来源": f"{task['source_file']} · {task['source_section']}",
            "AI判断": result.source_summary,
        })
        warnings.extend([f"{task['source_file']}：{warning}" for warning in result.warnings])
        for transaction in result.transactions:
            row = transaction.model_dump()
            row["source_file"] = task["source_file"]
            row["source_section"] = task["source_section"]
            records.append(row)

    progress.progress(1.0, text="AI 整理完成")
    if len(tasks) > max_calls:
        warnings.append(f"本次只处理前 {max_calls} 个资料区块，另有 {len(tasks) - max_calls} 个未处理。可提高最大调用次数后重跑。")
    return pd.DataFrame(records), warnings, summaries


def existing_keys(frame: pd.DataFrame) -> set[tuple[str, str, str, str, float]]:
    if frame.empty:
        return set()
    return {
        (
            str(row.date),
            str(row.item).strip().casefold(),
            str(row.category).strip().casefold(),
            str(row.type),
            round(float(row.amount), 2),
        )
        for row in frame.itertuples(index=False)
    }


def prepare_preview(raw_result: pd.DataFrame, existing: pd.DataFrame, confidence_threshold: float, append_source: bool) -> pd.DataFrame:
    columns = ["date", "item", "category", "type", "amount", "note", "currency", "confidence", "source_reference", "review_reason", "source_file", "source_section"]
    if raw_result.empty:
        return pd.DataFrame(columns=["导入"] + columns + ["状态"])
    frame = raw_result.copy()
    for column, default in {
        "date": None, "item": "", "category": "其他", "type": UNKNOWN, "amount": None,
        "note": "", "currency": "", "confidence": 0.0, "source_reference": "",
        "review_reason": "", "source_file": "", "source_section": "",
    }.items():
        if column not in frame.columns:
            frame[column] = default
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").abs().round(2)
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0).clip(0, 1)
    for column in ["item", "category", "type", "note", "currency", "source_reference", "review_reason", "source_file", "source_section"]:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame.loc[frame["category"].eq(""), "category"] = "其他"

    if append_source:
        provenance = frame.apply(
            lambda row: f"[AI导入 {row['source_file']} {row['source_reference'] or row['source_section']}]".strip(), axis=1
        )
        frame["note"] = [f"{note} {source}".strip() for note, source in zip(frame["note"], provenance)]

    keys = existing_keys(existing)
    status = []
    duplicate_flags = []
    for row in frame.itertuples(index=False):
        errors = []
        if pd.isna(row.date):
            errors.append("日期无效")
        if not str(row.item).strip():
            errors.append("项目为空")
        if row.type not in [EXPENSE, INCOME]:
            errors.append("类型不明")
        if pd.isna(row.amount) or float(row.amount) <= 0:
            errors.append("金额无效")
        key = None if errors else (
            row.date.date().isoformat(), str(row.item).strip().casefold(),
            str(row.category).strip().casefold(), str(row.type), round(float(row.amount), 2),
        )
        duplicate = key in keys if key else False
        duplicate_flags.append(duplicate)
        if errors:
            status.append("无法导入：" + "、".join(errors))
        elif duplicate:
            status.append("疑似重复")
        elif float(row.confidence) < confidence_threshold:
            status.append("需要复核")
        elif str(row.review_reason).strip():
            status.append("需要复核")
        else:
            status.append("可导入")

    frame["状态"] = status
    frame["_duplicate"] = duplicate_flags
    frame["导入"] = frame["状态"].eq("可导入")
    frame = frame.drop_duplicates(subset=["date", "item", "category", "type", "amount"], keep="first")
    return frame[["导入"] + columns + ["状态", "_duplicate"]]


def validate_edited(frame: pd.DataFrame, existing: pd.DataFrame, include_duplicates: bool) -> tuple[pd.DataFrame, list[str]]:
    selected = frame[frame["导入"]].copy()
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce")
    selected["amount"] = pd.to_numeric(selected["amount"], errors="coerce")
    errors = []
    invalid = selected[
        selected["date"].isna()
        | selected["item"].fillna("").astype(str).str.strip().eq("")
        | ~selected["type"].isin([EXPENSE, INCOME])
        | selected["amount"].isna()
        | (selected["amount"] <= 0)
    ]
    if not invalid.empty:
        errors.append(f"仍有 {len(invalid)} 笔选中记录缺少有效日期、项目、类型或金额。")

    if not include_duplicates:
        keys = existing_keys(existing)
        duplicate_mask = selected.apply(
            lambda row: (
                row["date"].date().isoformat(), str(row["item"]).strip().casefold(),
                str(row["category"]).strip().casefold(), str(row["type"]), round(float(row["amount"]), 2),
            ) in keys if pd.notna(row["date"]) and pd.notna(row["amount"]) else False,
            axis=1,
        )
        selected = selected[~duplicate_mask]
    return selected, errors


def create_missing_categories(categories: list[str]) -> None:
    existing = {value.casefold() for value in load_categories()}
    missing = sorted({str(value).strip() for value in categories if str(value).strip() and str(value).strip().casefold() not in existing})
    if missing:
        supabase.table("categories").insert([{"name": value} for value in missing]).execute()


def insert_transactions(frame: pd.DataFrame, chunk_size: int = 300) -> None:
    payload = []
    for row in frame.itertuples(index=False):
        payload.append({
            "date": row.date.date().isoformat(),
            "item": str(row.item).strip(),
            "category": str(row.category).strip() or "其他",
            "type": str(row.type),
            "amount": round(float(row.amount), 2),
            "note": str(row.note or "").strip(),
        })
    for start in range(0, len(payload), chunk_size):
        supabase.table("transactions").insert(payload[start:start + chunk_size]).execute()


st.markdown('<div class="ai-title">✨ AI 智能整理导入</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="ai-subtitle">使用 Gemini 3.6 Flash 理解混乱表格、账单、PDF、截图和文字，再整理成可检查的交易记录。</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="ai-callout">安全流程：上传 → AI 提取 → 人工预览与修改 → 重复检查 → 最后确认写入。AI 不会直接改动数据库。</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="ai-warning">隐私提示：点击开始整理后，上传内容会发送到 Google Gemini API 处理。请先移除不需要的身份证号、完整卡号、账号或其他敏感资料。</div>',
    unsafe_allow_html=True,
)

uploaded_files = st.file_uploader(
    "上传一个或多个资料",
    type=SUPPORTED_EXTENSIONS,
    accept_multiple_files=True,
    help="支持 CSV、Excel、JSON、TXT、Markdown、PDF、图片和 DOCX；单文件最多 20 MB，总计最多 50 MB。",
)

if not uploaded_files:
    st.info("上传后，系统会先在本地拆分表格或文字，再由 Gemini 3.6 Flash 提取交易。")
    st.stop()

total_bytes = sum(len(uploaded.getvalue()) for uploaded in uploaded_files)
if total_bytes > MAX_TOTAL_BYTES:
    st.error(f"上传总大小为 {total_bytes / 1024 / 1024:.1f} MB，超过 50 MB。请分批处理。")
    st.stop()

signature = file_signature(uploaded_files)
if st.session_state.get("ai_import_signature") != signature:
    st.session_state["ai_import_signature"] = signature
    st.session_state.pop("ai_import_raw_result", None)
    st.session_state.pop("ai_import_warnings", None)
    st.session_state.pop("ai_import_summaries", None)

st.markdown('<div class="ai-step">1. 整理规则</div>', unsafe_allow_html=True)
existing_categories = load_categories()
rule1, rule2, rule3, rule4 = st.columns(4)
default_currency = rule1.text_input("默认币种", value="MYR", help="资料没有写币种时使用。")
day_first = rule2.toggle("日／月／年优先", value=True, help="适合马来西亚常见日期。")
allow_new_categories = rule3.toggle("允许 AI 建议新类别", value=False)
append_source = rule4.toggle("备注保留来源", value=True)

rows_col, calls_col, confidence_col = st.columns(3)
rows_per_call = rows_col.slider("表格每批行数", min_value=50, max_value=300, value=150, step=25)
max_calls = calls_col.slider("本次最多 AI 调用", min_value=1, max_value=30, value=12)
confidence_threshold = confidence_col.slider("自动选中最低信心", min_value=0.50, max_value=0.95, value=0.78, step=0.01)
extra_instructions = st.text_area(
    "补充说明（可选）",
    placeholder="例如：退款算收入；所有 Grab 归类为交通；文档中的年份都是 2025；不要导入信用卡付款记录。",
)

try:
    tasks, read_warnings = build_tasks(uploaded_files, rows_per_call)
except Exception as exc:
    st.error(f"资料准备失败：{exc}")
    st.stop()

info1, info2, info3, info4 = st.columns(4)
info1.metric("文件", len(uploaded_files))
info2.metric("总大小", f"{total_bytes / 1024 / 1024:.1f} MB")
info3.metric("资料区块", len(tasks))
info4.metric("预计 AI 调用", min(len(tasks), max_calls))
for warning in read_warnings:
    st.warning(warning)
if not tasks:
    st.error("没有可交给 AI 处理的资料。")
    st.stop()

with st.expander("查看将处理的资料区块"):
    task_table = pd.DataFrame([
        {"文件": task["source_file"], "区域": task["source_section"], "形式": "PDF/图片" if task["kind"] == "binary" else "文字/表格"}
        for task in tasks
    ])
    st.dataframe(task_table, hide_index=True, use_container_width=True, height=320)

start_col, clear_col, _ = st.columns([1.3, 1, 3])
if start_col.button("使用 Gemini 3.6 Flash 开始整理", type="primary", use_container_width=True):
    with st.spinner("正在理解并整理资料，请勿关闭页面…"):
        raw_result, ai_warnings, summaries = run_extraction(
            tasks=tasks,
            categories=existing_categories,
            allow_new_categories=allow_new_categories,
            day_first=day_first,
            default_currency=default_currency.strip(),
            extra_instructions=extra_instructions.strip(),
            max_calls=max_calls,
        )
    st.session_state["ai_import_raw_result"] = raw_result
    st.session_state["ai_import_warnings"] = ai_warnings
    st.session_state["ai_import_summaries"] = summaries
    st.rerun()

if clear_col.button("清除 AI 结果", use_container_width=True):
    st.session_state.pop("ai_import_raw_result", None)
    st.session_state.pop("ai_import_warnings", None)
    st.session_state.pop("ai_import_summaries", None)
    st.rerun()

if "ai_import_raw_result" not in st.session_state:
    st.stop()

raw_result = st.session_state["ai_import_raw_result"]
ai_warnings = st.session_state.get("ai_import_warnings", [])
summaries = st.session_state.get("ai_import_summaries", [])

st.markdown('<div class="ai-step">2. AI 结果说明</div>', unsafe_allow_html=True)
if summaries:
    with st.expander("查看 AI 对每个资料区块的判断"):
        st.dataframe(pd.DataFrame(summaries), hide_index=True, use_container_width=True)
for warning in ai_warnings:
    st.warning(warning)
if raw_result.empty:
    st.info("AI 没有在本次资料中找到可确认的交易。可调整补充说明或提高最大调用次数后重试。")
    st.stop()

existing = load_existing_transactions()
preview = prepare_preview(raw_result, existing, confidence_threshold, append_source)

status_counts = preview["状态"].value_counts()
p1, p2, p3, p4, p5 = st.columns(5)
p1.metric("AI 提取", len(preview))
p2.metric("可导入", int(status_counts.get("可导入", 0)))
p3.metric("需要复核", int(status_counts.get("需要复核", 0)))
p4.metric("疑似重复", int(status_counts.get("疑似重复", 0)))
p5.metric("无法导入", int(preview["状态"].astype(str).str.startswith("无法导入").sum()))

st.markdown('<div class="ai-step">3. 人工检查与修改</div>', unsafe_allow_html=True)
st.caption("只有勾选“导入”的行才会写入数据库。低信心、日期不明和疑似重复默认不会被选中。")

category_options = sorted(set(existing_categories) | set(preview["category"].dropna().astype(str)))
editor_columns = [
    "导入", "date", "item", "category", "type", "amount", "note", "currency",
    "confidence", "状态", "review_reason", "source_file", "source_reference",
]
edited = st.data_editor(
    preview[editor_columns],
    hide_index=True,
    use_container_width=True,
    height=600,
    num_rows="fixed",
    disabled=["confidence", "状态", "source_file", "source_reference"],
    column_config={
        "导入": st.column_config.CheckboxColumn("导入"),
        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
        "item": st.column_config.TextColumn("项目／商家", required=True, width="large"),
        "category": st.column_config.SelectboxColumn("类别", options=category_options, required=True),
        "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, INCOME], required=True),
        "amount": st.column_config.NumberColumn("金额", min_value=0.01, format="RM %.2f", required=True),
        "note": st.column_config.TextColumn("备注", width="large"),
        "currency": st.column_config.TextColumn("币种", width="small"),
        "confidence": st.column_config.ProgressColumn("AI 信心", min_value=0.0, max_value=1.0, format="%.0f%%"),
        "状态": st.column_config.TextColumn("状态", width="medium"),
        "review_reason": st.column_config.TextColumn("复核原因", width="large"),
        "source_file": st.column_config.TextColumn("来源文件", width="medium"),
        "source_reference": st.column_config.TextColumn("来源位置", width="medium"),
    },
    key=f"ai_import_editor_{signature}",
)

csv_download = edited.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "下载 AI 整理结果 CSV",
    data=csv_download,
    file_name=f"WY_Wallet_AI整理_{date.today().isoformat()}.csv",
    mime="text/csv",
)

st.markdown('<div class="ai-step">4. 确认导入</div>', unsafe_allow_html=True)
opt1, opt2 = st.columns(2)
create_categories = opt1.toggle("自动建立新类别", value=allow_new_categories)
include_duplicates = opt2.toggle("允许导入疑似重复", value=False)
selected, validation_errors = validate_edited(edited, existing, include_duplicates)

s1, s2, s3 = st.columns(3)
s1.metric("当前选中", len(edited[edited["导入"]]))
s2.metric("检查后可写入", len(selected))
s3.metric("预计支出合计", f"RM {selected.loc[selected['type'] == EXPENSE, 'amount'].sum():,.2f}" if not selected.empty else "RM 0.00")
for error in validation_errors:
    st.error(error)

confirm = st.checkbox(
    f"我已检查资料，并确认将 {len(selected)} 笔记录新增到现有 Supabase 数据库。",
    disabled=selected.empty or bool(validation_errors),
)
if st.button(
    "确认导入选中记录",
    type="primary",
    use_container_width=True,
    disabled=not confirm or selected.empty or bool(validation_errors),
):
    try:
        with st.spinner("正在写入 Supabase…"):
            if create_categories:
                create_missing_categories(selected["category"].astype(str).tolist())
            insert_transactions(selected)
            clear_cache()
        st.success(f"成功导入 {len(selected)} 笔交易。")
        st.session_state.pop("ai_import_raw_result", None)
        st.session_state.pop("ai_import_warnings", None)
        st.session_state.pop("ai_import_summaries", None)
        st.balloons()
    except Exception as exc:
        st.error(f"导入失败：{exc}。数据库可能已写入部分批次，请先到交易记录检查后再重试。")
