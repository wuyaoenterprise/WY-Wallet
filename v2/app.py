import calendar
import io
import json
from datetime import date, datetime

import google.generativeai as genai
import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image
from supabase import create_client


APP_TITLE = "WY Wallet V2"
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
ADD_CATEGORY_OPTION = "＋ 新增类别"
EXPENSE = "Expense"
INCOME = "Income"
TYPE_LABELS = {EXPENSE: "支出", INCOME: "收入"}
MONTH_LABELS = [f"{month}月" for month in range(1, 13)]

st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --wy-primary: #5b8ff9;
        --wy-positive: #32b67a;
        --wy-negative: #f06464;
        --wy-border: rgba(128, 128, 128, 0.24);
        --wy-muted: rgba(160, 166, 180, 0.82);
    }
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width: 1240px;
        padding-top: 1.25rem;
        padding-bottom: 3rem;
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid var(--wy-border);
    }
    .wy-brand {
        padding: 0.45rem 0 1rem;
    }
    .wy-brand-title {
        font-size: 1.45rem;
        font-weight: 800;
        line-height: 1.2;
        letter-spacing: -0.02em;
    }
    .wy-brand-subtitle, .wy-muted {
        color: var(--wy-muted);
        font-size: 0.88rem;
    }
    .wy-page-title {
        font-size: 1.85rem;
        font-weight: 800;
        letter-spacing: -0.03em;
        margin: 0 0 0.15rem;
    }
    .wy-page-subtitle {
        color: var(--wy-muted);
        margin-bottom: 1.2rem;
    }
    .wy-section-title {
        font-size: 1.04rem;
        font-weight: 750;
        margin: 0.2rem 0 0.65rem;
    }
    div[data-testid="stMetric"] {
        border: 1px solid var(--wy-border);
        border-radius: 14px;
        padding: 0.85rem 1rem;
        background: rgba(127, 127, 127, 0.035);
    }
    div[data-testid="stMetricLabel"] {
        color: var(--wy-muted);
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.45rem;
    }
    .wy-detail {
        border: 1px solid var(--wy-border);
        border-radius: 14px;
        padding: 1rem 1.05rem;
        margin-top: 0.7rem;
        background: rgba(127, 127, 127, 0.025);
    }
    .wy-amount-expense { color: var(--wy-negative); font-weight: 800; }
    .wy-amount-income { color: var(--wy-positive); font-weight: 800; }
    .wy-chip {
        display: inline-block;
        border: 1px solid var(--wy-border);
        border-radius: 999px;
        padding: 0.12rem 0.5rem;
        font-size: 0.78rem;
        color: var(--wy-muted);
    }
    .wy-empty {
        border: 1px dashed var(--wy-border);
        border-radius: 14px;
        padding: 2rem;
        text-align: center;
        color: var(--wy-muted);
    }
    .wy-calendar {
        display: grid;
        grid-template-columns: repeat(7, minmax(0, 1fr));
        gap: 6px;
    }
    .wy-calendar-head {
        text-align: center;
        color: var(--wy-muted);
        font-size: 0.78rem;
        padding: 0.25rem;
    }
    .wy-calendar-day {
        min-height: 68px;
        border: 1px solid var(--wy-border);
        border-radius: 10px;
        padding: 0.45rem;
    }
    .wy-calendar-date {
        color: var(--wy-muted);
        font-size: 0.78rem;
    }
    .wy-calendar-amount {
        font-size: 0.88rem;
        font-weight: 750;
        margin-top: 0.45rem;
    }
    @media (max-width: 760px) {
        [data-testid="stAppViewContainer"] > .main .block-container {
            padding-left: 0.75rem;
            padding-right: 0.75rem;
        }
        .wy-page-title { font-size: 1.55rem; }
        .wy-calendar { gap: 3px; }
        .wy-calendar-day { min-height: 48px; padding: 0.25rem; }
        .wy-calendar-amount { font-size: 0.68rem; margin-top: 0.2rem; }
    }
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


@st.cache_data(ttl=300, show_spinner=False)
def load_transactions() -> pd.DataFrame:
    columns = ["id", "date", "item", "category", "type", "amount", "note"]
    try:
        response = (
            supabase.table("transactions")
            .select("*")
            .order("date", desc=True)
            .order("id", desc=True)
            .execute()
        )
        frame = pd.DataFrame(response.data)
        if frame.empty:
            return pd.DataFrame(columns=columns)

        defaults = {
            "item": "未知",
            "category": "其他",
            "type": EXPENSE,
            "amount": 0.0,
            "note": "",
        }
        for column, default in defaults.items():
            if column not in frame.columns:
                frame[column] = default

        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
        frame["item"] = frame["item"].fillna("未知").astype(str)
        frame["category"] = frame["category"].fillna("其他").astype(str)
        frame["type"] = frame["type"].where(frame["type"].isin([EXPENSE, INCOME]), EXPENSE)
        frame["note"] = frame["note"].fillna("").astype(str)
        return frame.dropna(subset=["date"]).sort_values(["date", "id"], ascending=[False, False])
    except Exception as exc:
        st.session_state["database_error"] = str(exc)
        return pd.DataFrame(columns=columns)


@st.cache_data(ttl=1800, show_spinner=False)
def load_categories() -> list[str]:
    try:
        response = supabase.table("categories").select("name").execute()
        values = [str(row["name"]).strip() for row in response.data if str(row.get("name", "")).strip()]
        return values or DEFAULT_CATEGORIES.copy()
    except Exception:
        return DEFAULT_CATEGORIES.copy()


def clear_data_cache() -> None:
    load_transactions.clear()
    load_categories.clear()


def normalize_transactions(rows) -> list[dict]:
    records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
    normalized = []
    for row in records:
        amount = pd.to_numeric(row.get("amount"), errors="coerce")
        parsed_date = pd.to_datetime(row.get("date", date.today()), errors="coerce")
        if pd.isna(amount) or pd.isna(parsed_date):
            raise ValueError("日期或金额格式错误。")
        normalized.append(
            {
                "date": parsed_date.date().isoformat(),
                "item": str(row.get("item") or "未知").strip(),
                "category": str(row.get("category") or "其他").strip(),
                "type": INCOME if str(row.get("type")) == INCOME else EXPENSE,
                "amount": float(amount),
                "note": str(row.get("note") or "").strip(),
            }
        )
    return normalized


def insert_transactions(rows, toast: bool = True) -> bool:
    try:
        records = normalize_transactions(rows)
        if not records:
            raise ValueError("没有可保存的记录。")
        supabase.table("transactions").insert(records).execute()
        clear_data_cache()
        if toast:
            st.toast(f"已保存 {len(records)} 笔记录")
        return True
    except Exception as exc:
        st.error(f"保存失败：{exc}")
        return False


def update_transaction(transaction_id: int, row: dict) -> bool:
    try:
        payload = normalize_transactions([row])[0]
        supabase.table("transactions").update(payload).eq("id", int(transaction_id)).execute()
        clear_data_cache()
        st.toast("交易已更新")
        return True
    except Exception as exc:
        st.error(f"修改失败：{exc}")
        return False


def delete_transaction(row: dict) -> bool:
    try:
        supabase.table("transactions").delete().eq("id", int(row["id"])).execute()
        st.session_state["recently_deleted"] = normalize_transactions([row])[0]
        clear_data_cache()
        st.toast("交易已删除，可在本页撤销")
        return True
    except Exception as exc:
        st.error(f"删除失败：{exc}")
        return False


def create_category(name: str) -> bool:
    cleaned = str(name or "").strip()
    existing = {category.casefold() for category in load_categories()}
    if not cleaned:
        st.warning("请输入类别名称。")
        return False
    if cleaned.casefold() in existing:
        st.warning("这个类别已经存在。")
        return False
    try:
        supabase.table("categories").insert({"name": cleaned}).execute()
        clear_data_cache()
        st.toast(f"已新增类别：{cleaned}")
        return True
    except Exception as exc:
        st.error(f"新增类别失败：{exc}")
        return False


def rename_or_merge_category(source: str, target: str) -> bool:
    source = str(source).strip()
    target = str(target).strip()
    if not source or not target or source == target:
        st.warning("请选择不同的原类别和目标类别。")
        return False
    try:
        if target.casefold() not in {category.casefold() for category in load_categories()}:
            supabase.table("categories").insert({"name": target}).execute()
        supabase.table("transactions").update({"category": target}).eq("category", source).execute()
        supabase.table("categories").delete().eq("name", source).execute()
        clear_data_cache()
        st.toast(f"已将“{source}”合并到“{target}”")
        return True
    except Exception as exc:
        st.error(f"合并类别失败：{exc}")
        return False


def money(value: float, signed: bool = False) -> str:
    number = float(value or 0)
    if signed:
        return f"{'+' if number >= 0 else '−'}RM {abs(number):,.2f}"
    return f"RM {number:,.2f}"


def month_slice(frame: pd.DataFrame, year: int, month_number: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame[(frame["date"].dt.year == int(year)) & (frame["date"].dt.month == int(month_number))].copy()


def calculate_totals(frame: pd.DataFrame) -> tuple[float, float, float]:
    income = float(frame.loc[frame["type"] == INCOME, "amount"].sum())
    expense = float(frame.loc[frame["type"] == EXPENSE, "amount"].sum())
    return income, expense, income - expense


def monthly_summary(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    base = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS})
    year_data = frame[frame["date"].dt.year == int(year)].copy()
    if year_data.empty:
        base["收入"] = 0.0
        base["支出"] = 0.0
    else:
        grouped = (
            year_data.assign(month=year_data["date"].dt.month)
            .pivot_table(index="month", columns="type", values="amount", aggfunc="sum", fill_value=0)
            .reset_index()
        )
        if INCOME not in grouped.columns:
            grouped[INCOME] = 0.0
        if EXPENSE not in grouped.columns:
            grouped[EXPENSE] = 0.0
        grouped = grouped.rename(columns={INCOME: "收入", EXPENSE: "支出"})
        base = base.merge(grouped[["month", "收入", "支出"]], on="month", how="left").fillna(0)
    base["结余"] = base["收入"] - base["支出"]
    return base


def previous_month(year: int, month_number: int) -> tuple[int, int]:
    return (year - 1, 12) if month_number == 1 else (year, month_number - 1)


def chart_layout(fig, height: int = 360, show_legend: bool = False):
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=28, b=8),
        legend_title_text="",
        showlegend=show_legend,
        hovermode="x unified",
    )
    return fig


def clean_json(text: str):
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())


def analyze_receipt(image: Image.Image):
    try:
        prompt = f"""
        读取这张收据并逐项拆分。只返回 JSON 数组，不要 Markdown。
        每一项格式：
        {{"date":"YYYY-MM-DD","item":"简洁中文名称","category":"类别","amount":10.5,"type":"Expense","note":""}}
        category 必须从以下类别中选择：{load_categories()}
        无法判断日期时使用 {date.today().isoformat()}。
        折扣直接计入对应项目，不要创建虚假的付款项目。
        """
        response = genai.GenerativeModel("gemini-3.5-flash").generate_content([prompt, image])
        result = clean_json(response.text)
        return (result if isinstance(result, list) else [result]), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=86400, show_spinner=False)
def categorize_macro(items_json: str) -> dict:
    try:
        prompt = f"""
        将输入项目归类为以下一个宏观类别：
        餐饮美食、交通出行、居家生活、购物消费、休闲娱乐、
        医疗健康、教育学习、投资理财、旅游度假、其他。
        只返回 JSON 对象，例如 {{"KFC":"餐饮美食"}}。
        输入：{items_json}
        """
        response = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt)
        result = clean_json(response.text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    st.caption("记录现金、转账或手动补录的收入与支出。")
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=date.today(), key="add_date")
    tx_type = col_type.segmented_control(
        "类型", options=[EXPENSE, INCOME], default=EXPENSE,
        format_func=lambda value: TYPE_LABELS[value], key="add_type"
    )
    category_options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", category_options, key="add_category")
    new_category_name = ""
    if selected_category == ADD_CATEGORY_OPTION:
        new_category_name = st.text_input(
            "新类别名称", placeholder="保存交易时会同时建立此类别", key="add_new_category"
        )
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="add_item")
    amount = st.number_input(
        "金额 (RM)", min_value=0.0, step=0.01, value=None,
        placeholder="0.00", key="add_amount"
    )
    note = st.text_area("备注（可选）", key="add_note")
    if st.button("保存交易", type="primary", use_container_width=True):
        if not item.strip():
            st.warning("请输入项目或商家。")
        elif amount is None or amount <= 0:
            st.warning("金额必须大于 0。")
        else:
            effective_category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
            if not effective_category:
                st.warning("请输入新类别名称。")
            else:
                category_ready = True
                if selected_category == ADD_CATEGORY_OPTION:
                    category_ready = create_category(effective_category)
                if category_ready and insert_transactions([{
                    "date": tx_date, "item": item, "category": effective_category,
                    "type": tx_type or EXPENSE, "amount": amount, "note": note
                }]):
                    st.rerun()


@st.dialog("编辑交易", width="large")
def edit_transaction_dialog(transaction_id: int, categories: list[str]) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易，它可能已经被删除。")
        return
    row = match.iloc[0]
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=row["date"].date(), key=f"edit_date_{transaction_id}")
    tx_type = col_type.segmented_control(
        "类型", options=[EXPENSE, INCOME], default=row["type"],
        format_func=lambda value: TYPE_LABELS[value], key=f"edit_type_{transaction_id}"
    )
    options = categories.copy()
    if row["category"] not in options:
        options.insert(0, row["category"])
    options.append(ADD_CATEGORY_OPTION)
    selected_category = st.selectbox(
        "类别", options, index=options.index(row["category"]), key=f"edit_category_{transaction_id}"
    )
    new_category_name = ""
    if selected_category == ADD_CATEGORY_OPTION:
        new_category_name = st.text_input(
            "新类别名称", placeholder="保存修改时会同时建立此类别",
            key=f"edit_new_category_{transaction_id}"
        )
    item = st.text_input("项目或商家", value=row["item"], key=f"edit_item_{transaction_id}")
    amount = st.number_input(
        "金额 (RM)", min_value=0.0, step=0.01,
        value=float(row["amount"]), key=f"edit_amount_{transaction_id}"
    )
    note = st.text_area("备注", value=row["note"], key=f"edit_note_{transaction_id}")
    if st.button("保存修改", type="primary", use_container_width=True):
        if not item.strip() or amount <= 0:
            st.warning("请填写项目，且金额必须大于 0。")
        else:
            effective_category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
            if not effective_category:
                st.warning("请输入新类别名称。")
            else:
                category_ready = True
                if selected_category == ADD_CATEGORY_OPTION:
                    category_ready = create_category(effective_category)
                if category_ready and update_transaction(transaction_id, {
                    "date": tx_date, "item": item, "category": effective_category,
                    "type": tx_type or EXPENSE, "amount": amount, "note": note
                }):
                    st.rerun()


@st.dialog("删除交易")
def delete_transaction_dialog(transaction_id: int) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易。")
        return
    row = match.iloc[0].to_dict()
    st.warning("删除后会从 Supabase 移除，但本次浏览期间可以撤销一次。")
    st.write(f"**{row['item']}**")
    st.caption(f"{row['date'].date()} · {row['category']} · {money(row['amount'])}")
    confirm = st.checkbox("我确认删除这笔交易", key=f"confirm_delete_{transaction_id}")
    if st.button(
        "确认删除", type="primary", disabled=not confirm,
        use_container_width=True, key=f"delete_{transaction_id}"
    ):
        if delete_transaction(row):
            st.rerun()


transactions = load_transactions()
categories = load_categories()
usage_counts = transactions["category"].value_counts().to_dict() if not transactions.empty else {}
categories = sorted(categories, key=lambda value: (-usage_counts.get(value, 0), value))

if st.session_state.get("database_error"):
    st.error("数据库读取失败：" + st.session_state["database_error"])

with st.sidebar:
    st.markdown(
        """
        <div class="wy-brand">
            <div class="wy-brand-title">💳 WY Wallet</div>
            <div class="wy-brand-subtitle">个人财务中心 · V2</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    navigation = st.radio(
        "导航",
        options=["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"],
        format_func=lambda value: {
            "总览": "⌂  总览",
            "交易记录": "≡  交易记录",
            "分析报表": "▥  分析报表",
            "AI 洞察": "✦  AI 洞察",
            "设置与备份": "⚙  设置与备份",
        }[value],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("V2 独立部署")
    st.caption("继续使用现有 Supabase 数据")


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="wy-page-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="wy-page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


if navigation == "总览":
    page_header("财务总览", "快速了解当前月份的现金流、趋势与主要支出。")
    now = datetime.now()
    current_month = month_slice(transactions, now.year, now.month)
    previous_year, previous_month_number = previous_month(now.year, now.month)
    prior_month = month_slice(transactions, previous_year, previous_month_number)
    income, expense, balance = calculate_totals(current_month)
    _, prior_expense, _ = calculate_totals(prior_month)
    expense_change = None if prior_expense == 0 else (expense - prior_expense) / prior_expense

    metric_income, metric_expense, metric_balance, metric_average = st.columns(4)
    metric_income.metric("本月收入", money(income))
    metric_expense.metric(
        "本月支出", money(expense),
        "无上月数据" if expense_change is None else f"{expense_change:+.1%} 对比上月",
        delta_color="inverse",
    )
    metric_balance.metric("本月结余", money(balance))
    metric_average.metric("日均支出", money(expense / max(now.day, 1)))

    st.write("")
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        st.markdown('<div class="wy-section-title">最近 12 个月支出</div>', unsafe_allow_html=True)
        month_ends = pd.period_range(end=pd.Period(now, freq="M"), periods=12, freq="M")
        twelve_months = pd.DataFrame({
            "period": month_ends,
            "月份": [period.strftime("%Y-%m") for period in month_ends],
        })
        expense_data = transactions[transactions["type"] == EXPENSE].copy()
        if not expense_data.empty:
            expense_data["period"] = expense_data["date"].dt.to_period("M")
            grouped = expense_data.groupby("period")["amount"].sum().reset_index()
            twelve_months = twelve_months.merge(grouped, on="period", how="left")
        else:
            twelve_months["amount"] = 0.0
        twelve_months["amount"] = twelve_months["amount"].fillna(0.0)
        max_expense = max(float(twelve_months["amount"].max()), 1.0)
        fig = px.bar(
            twelve_months, x="月份", y="amount",
            labels={"amount": "支出 (RM)"}, text_auto=".0f",
            color_discrete_sequence=["#5B8FF9"],
        )
        fig.update_traces(hovertemplate="%{x}<br>支出 RM %{y:,.2f}<extra></extra>")
        fig.update_yaxes(range=[0, max_expense * 1.18], rangemode="tozero", tickprefix="RM ")
        chart_layout(fig, height=340)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with right:
        st.markdown('<div class="wy-section-title">本月支出类别</div>', unsafe_allow_html=True)
        current_expenses = current_month[current_month["type"] == EXPENSE]
        category_summary = (
            current_expenses.groupby("category")["amount"]
            .sum().sort_values(ascending=False).head(7)
        )
        if category_summary.empty:
            st.markdown('<div class="wy-empty">本月暂无支出</div>', unsafe_allow_html=True)
        else:
            for category_name, value in category_summary.items():
                ratio = float(value / expense) if expense else 0.0
                label_col, value_col = st.columns([1.6, 1])
                label_col.write(f"**{category_name}**")
                value_col.write(f"<div style='text-align:right'>{money(value)}</div>", unsafe_allow_html=True)
                st.progress(min(ratio, 1.0))

    st.write("")
    st.markdown('<div class="wy-section-title">最近交易</div>', unsafe_allow_html=True)
    if transactions.empty:
        st.markdown('<div class="wy-empty">还没有任何交易记录</div>', unsafe_allow_html=True)
    else:
        recent = transactions.head(8).copy()
        recent["日期"] = recent["date"].dt.strftime("%Y-%m-%d")
        recent["项目"] = recent["item"]
        recent["类别"] = recent["category"]
        recent["类型"] = recent["type"].map(TYPE_LABELS)
        recent["金额"] = recent.apply(
            lambda row: row["amount"] if row["type"] == INCOME else -row["amount"], axis=1
        )
        st.dataframe(
            recent[["日期", "项目", "类别", "类型", "金额"]],
            hide_index=True, use_container_width=True, height=315,
            column_config={
                "日期": st.column_config.TextColumn(width="small"),
                "项目": st.column_config.TextColumn(width="large"),
                "类别": st.column_config.TextColumn(width="medium"),
                "类型": st.column_config.TextColumn(width="small"),
                "金额": st.column_config.NumberColumn(format="RM %.2f"),
            },
        )


elif navigation == "交易记录":
    page_header("交易记录", "统一搜索、筛选、检查与编辑所有账目。")
    action_add, action_receipt, action_undo, action_space = st.columns([1, 1.15, 1.25, 3.6])
    if action_add.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    show_receipt = action_receipt.toggle("收据识别", key="show_receipt")
    if st.session_state.get("recently_deleted"):
        if action_undo.button("↩ 撤销最近删除", use_container_width=True):
            if insert_transactions([st.session_state["recently_deleted"]]):
                del st.session_state["recently_deleted"]
                st.rerun()

    if show_receipt:
        with st.container(border=True):
            st.markdown('<div class="wy-section-title">AI 收据识别</div>', unsafe_allow_html=True)
            upload_col, category_col = st.columns([2, 1])
            with category_col:
                with st.popover("＋ 新增收据类别", use_container_width=True):
                    new_receipt_category = st.text_input("类别名称", key="receipt_new_category")
                    if st.button("新增类别", key="receipt_create_category", use_container_width=True):
                        if create_category(new_receipt_category):
                            st.rerun()
            uploaded_receipt = upload_col.file_uploader(
                "上传 JPG、JPEG 或 PNG", type=["jpg", "jpeg", "png"], key="receipt_upload"
            )
            if uploaded_receipt is not None:
                preview, action = st.columns([1, 1.5])
                preview.image(uploaded_receipt, use_container_width=True)
                if action.button("开始识别", type="primary", use_container_width=True):
                    with st.spinner("正在识别收据项目..."):
                        result, error = analyze_receipt(Image.open(uploaded_receipt))
                    if error:
                        st.error(error)
                    else:
                        st.session_state["pending_receipt"] = result
                        st.rerun()
            if "pending_receipt" in st.session_state:
                pending = pd.DataFrame(st.session_state["pending_receipt"])
                defaults = {
                    "date": date.today(), "item": "", "category": "其他",
                    "type": EXPENSE, "amount": 0.0, "note": "",
                }
                for column, default in defaults.items():
                    if column not in pending.columns:
                        pending[column] = default
                pending["date"] = pd.to_datetime(pending["date"], errors="coerce").fillna(pd.Timestamp(date.today()))
                edited_pending = st.data_editor(
                    pending[["date", "item", "category", "type", "amount", "note"]],
                    num_rows="dynamic", hide_index=True, use_container_width=True,
                    column_config={
                        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                        "item": st.column_config.TextColumn("项目", required=True),
                        "category": st.column_config.SelectboxColumn("类别", options=categories, required=True),
                        "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, INCOME], required=True),
                        "amount": st.column_config.NumberColumn("金额 (RM)", min_value=0.0, format="%.2f", required=True),
                        "note": st.column_config.TextColumn("备注"),
                    },
                )
                save_col, discard_col, spacer = st.columns([1, 1, 3])
                if save_col.button("确认保存", type="primary", use_container_width=True):
                    if insert_transactions(edited_pending):
                        del st.session_state["pending_receipt"]
                        st.rerun()
                if discard_col.button("放弃识别", use_container_width=True):
                    del st.session_state["pending_receipt"]
                    st.rerun()

    with st.expander("筛选交易", expanded=True):
        search_col, year_col, month_col = st.columns([2, 1, 1])
        search_text = search_col.text_input("搜索", placeholder="项目、类别或备注", key="transaction_search")
        available_years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        selected_year = year_col.selectbox("年份", ["全部"] + available_years)
        selected_month = month_col.selectbox("月份", ["全部"] + list(range(1, 13)))
        type_col, category_col, sort_col = st.columns([1, 1.5, 1.5])
        selected_type = type_col.selectbox(
            "类型", ["全部", EXPENSE, INCOME],
            format_func=lambda value: "全部" if value == "全部" else TYPE_LABELS[value]
        )
        selected_category = category_col.selectbox("类别", ["全部"] + categories)
        selected_sort = sort_col.selectbox(
            "排序", ["日期：最新优先", "日期：最早优先", "金额：由高到低", "金额：由低到高"]
        )

    filtered = transactions.copy()
    if search_text:
        search_mask = (
            filtered["item"].str.contains(search_text, case=False, na=False)
            | filtered["category"].str.contains(search_text, case=False, na=False)
            | filtered["note"].str.contains(search_text, case=False, na=False)
        )
        filtered = filtered[search_mask]
    if selected_year != "全部":
        filtered = filtered[filtered["date"].dt.year == int(selected_year)]
    if selected_month != "全部":
        filtered = filtered[filtered["date"].dt.month == int(selected_month)]
    if selected_type != "全部":
        filtered = filtered[filtered["type"] == selected_type]
    if selected_category != "全部":
        filtered = filtered[filtered["category"] == selected_category]

    sort_rules = {
        "日期：最新优先": ("date", False),
        "日期：最早优先": ("date", True),
        "金额：由高到低": ("amount", False),
        "金额：由低到高": ("amount", True),
    }
    sort_column, ascending = sort_rules[selected_sort]
    filtered = filtered.sort_values([sort_column, "id"], ascending=[ascending, ascending])
    filtered_income, filtered_expense, filtered_balance = calculate_totals(filtered)
    count_metric, expense_metric, income_metric, balance_metric = st.columns(4)
    count_metric.metric("筛选结果", f"{len(filtered):,} 笔")
    expense_metric.metric("支出", money(filtered_expense))
    income_metric.metric("收入", money(filtered_income))
    balance_metric.metric("净额", money(filtered_balance))
    st.caption("点击表格中的一行，可在下方查看、编辑或删除。")

    table = filtered.copy()
    table["日期"] = table["date"].dt.strftime("%Y-%m-%d")
    table["项目"] = table["item"]
    table["类别"] = table["category"]
    table["类型"] = table["type"].map(TYPE_LABELS)
    table["金额"] = table.apply(lambda row: row["amount"] if row["type"] == INCOME else -row["amount"], axis=1)
    table["备注"] = table["note"]
    table["_id"] = table["id"]
    event = st.dataframe(
        table[["_id", "日期", "项目", "类别", "类型", "金额", "备注"]],
        column_order=["日期", "项目", "类别", "类型", "金额", "备注"],
        hide_index=True, use_container_width=True, height=560,
        on_select="rerun", selection_mode="single-row", key="transaction_table",
        column_config={
            "日期": st.column_config.TextColumn(width="small"),
            "项目": st.column_config.TextColumn(width="large"),
            "类别": st.column_config.TextColumn(width="medium"),
            "类型": st.column_config.TextColumn(width="small"),
            "金额": st.column_config.NumberColumn(format="RM %.2f", width="medium"),
            "备注": st.column_config.TextColumn(width="large"),
        },
    )
    selected_rows = event.selection.rows
    if selected_rows and selected_rows[0] < len(table):
        selected = table.iloc[selected_rows[0]]
        original = filtered[filtered["id"] == selected["_id"]].iloc[0]
        amount_class = "wy-amount-income" if original["type"] == INCOME else "wy-amount-expense"
        sign = "+" if original["type"] == INCOME else "−"
        st.markdown(
            f"""
            <div class="wy-detail">
                <span class="wy-chip">{TYPE_LABELS[original['type']]}</span>
                <span class="wy-chip">{original['category']}</span>
                <h3 style="margin:0.55rem 0 0.2rem">{original['item']}</h3>
                <div class="{amount_class}" style="font-size:1.35rem">{sign}{money(original['amount'])}</div>
                <div class="wy-muted" style="margin-top:0.35rem">
                    {original['date'].date()}{f" · {original['note']}" if original['note'] else ""}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        edit_col, delete_col, detail_spacer = st.columns([1, 1, 4])
        if edit_col.button("编辑交易", type="primary", use_container_width=True):
            edit_transaction_dialog(int(original["id"]), categories)
        if delete_col.button("删除交易", use_container_width=True):
            delete_transaction_dialog(int(original["id"]))


elif navigation == "分析报表":
    page_header("分析报表", "先看完整年度趋势，再深入到月份、类别与单笔交易。")
    if transactions.empty:
        st.markdown('<div class="wy-empty">暂无数据可分析</div>', unsafe_allow_html=True)
    else:
        available_years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
        default_year_index = available_years.index(datetime.now().year) if datetime.now().year in available_years else 0
        report_year = st.selectbox("分析年份", available_years, index=default_year_index)
        annual = monthly_summary(transactions, int(report_year))
        annual_expense = float(annual["支出"].sum())
        annual_income = float(annual["收入"].sum())
        active_months = max(int((annual["支出"] > 0).sum()), 1)
        average_expense = annual_expense / active_months
        highest_month_row = annual.loc[annual["支出"].idxmax()]
        prior_annual = monthly_summary(transactions, int(report_year) - 1)
        prior_total = float(prior_annual["支出"].sum())
        year_change = None if prior_total == 0 else (annual_expense - prior_total) / prior_total

        annual_expense_metric, annual_income_metric, average_metric, high_metric = st.columns(4)
        annual_expense_metric.metric(
            "年度支出", money(annual_expense),
            "无去年数据" if year_change is None else f"{year_change:+.1%} 同比",
            delta_color="inverse",
        )
        annual_income_metric.metric("年度收入", money(annual_income))
        average_metric.metric("月均支出", money(average_expense))
        high_metric.metric("最高支出月份", str(highest_month_row["月份"]), money(highest_month_row["支出"]), delta_color="off")

        annual_tab, monthly_tab, category_tab = st.tabs(["12 个月趋势", "月度明细", "类别与项目"])
        with annual_tab:
            st.markdown('<div class="wy-section-title">全年每月支出</div>', unsafe_allow_html=True)
            st.caption("固定显示 1–12 月；纵轴从 RM 0 开始，避免视觉误导。")
            max_annual_expense = max(float(annual["支出"].max()), 1.0)
            annual_bar = px.bar(
                annual, x="月份", y="支出", text_auto=".0f",
                color_discrete_sequence=["#5B8FF9"]
            )
            annual_bar.add_hline(
                y=average_expense, line_dash="dash", line_color="#F6BD16",
                annotation_text=f"月均 {money(average_expense)}", annotation_position="top left"
            )
            annual_bar.update_traces(hovertemplate="%{x}<br>支出 RM %{y:,.2f}<extra></extra>")
            annual_bar.update_yaxes(range=[0, max_annual_expense * 1.2], rangemode="tozero", tickprefix="RM ")
            chart_layout(annual_bar, height=430)
            st.plotly_chart(annual_bar, use_container_width=True, config={"displayModeBar": False})

            st.markdown('<div class="wy-section-title">收入与结余</div>', unsafe_allow_html=True)
            cashflow = annual.melt(
                id_vars=["month", "月份"], value_vars=["收入", "结余"],
                var_name="指标", value_name="金额"
            )
            cashflow_chart = px.line(
                cashflow, x="月份", y="金额", color="指标", markers=True,
                color_discrete_map={"收入": "#32B67A", "结余": "#F6BD16"}
            )
            cashflow_chart.update_yaxes(rangemode="tozero", tickprefix="RM ")
            chart_layout(cashflow_chart, height=350, show_legend=True)
            st.plotly_chart(cashflow_chart, use_container_width=True, config={"displayModeBar": False})

        with monthly_tab:
            month_selector, monthly_space = st.columns([1, 3])
            report_month = month_selector.selectbox(
                "选择月份", range(1, 13), index=datetime.now().month - 1,
                format_func=lambda value: f"{value}月"
            )
            selected_month_data = month_slice(transactions, int(report_year), int(report_month))
            month_income, month_expense, month_balance = calculate_totals(selected_month_data)
            month_income_metric, month_expense_metric, month_balance_metric, month_count_metric = st.columns(4)
            month_income_metric.metric("收入", money(month_income))
            month_expense_metric.metric("支出", money(month_expense))
            month_balance_metric.metric("结余", money(month_balance))
            month_count_metric.metric("交易笔数", f"{len(selected_month_data):,}")

            month_expenses = selected_month_data[selected_month_data["type"] == EXPENSE].copy()
            days_in_month = calendar.monthrange(int(report_year), int(report_month))[1]
            daily_base = pd.DataFrame({"day": range(1, days_in_month + 1)})
            if not month_expenses.empty:
                daily_grouped = (
                    month_expenses.assign(day=month_expenses["date"].dt.day)
                    .groupby("day")["amount"].sum().reset_index()
                )
                daily_base = daily_base.merge(daily_grouped, on="day", how="left")
            else:
                daily_base["amount"] = 0.0
            daily_base["amount"] = daily_base["amount"].fillna(0.0)
            st.markdown('<div class="wy-section-title">每日支出</div>', unsafe_allow_html=True)
            daily_max = max(float(daily_base["amount"].max()), 1.0)
            daily_chart = px.bar(
                daily_base, x="day", y="amount",
                labels={"day": "日期", "amount": "支出 (RM)"},
                color_discrete_sequence=["#5B8FF9"]
            )
            daily_chart.update_xaxes(dtick=1)
            daily_chart.update_yaxes(range=[0, daily_max * 1.18], rangemode="tozero", tickprefix="RM ")
            daily_chart.update_traces(hovertemplate=f"{report_month}月 %{{x}}日<br>支出 RM %{{y:,.2f}}<extra></extra>")
            chart_layout(daily_chart, height=370)
            st.plotly_chart(daily_chart, use_container_width=True, config={"displayModeBar": False})

            with st.expander("查看月历"):
                daily_sums = (
                    month_expenses.groupby(month_expenses["date"].dt.day)["amount"].sum().to_dict()
                    if not month_expenses.empty else {}
                )
                headers = "".join(
                    f'<div class="wy-calendar-head">{name}</div>'
                    for name in ["一", "二", "三", "四", "五", "六", "日"]
                )
                cells = []
                for week in calendar.monthcalendar(int(report_year), int(report_month)):
                    for day_number in week:
                        if day_number == 0:
                            cells.append('<div class="wy-calendar-day" style="opacity:.22"></div>')
                        else:
                            value = daily_sums.get(day_number, 0.0)
                            amount_html = f'<div class="wy-calendar-amount">{money(value)}</div>' if value else ""
                            cells.append(
                                f'<div class="wy-calendar-day"><div class="wy-calendar-date">{day_number}</div>{amount_html}</div>'
                            )
                st.markdown(f'<div class="wy-calendar">{headers}{"".join(cells)}</div>', unsafe_allow_html=True)

        with category_tab:
            year_expenses = transactions[
                (transactions["date"].dt.year == int(report_year))
                & (transactions["type"] == EXPENSE)
            ].copy()
            category_col, item_col = st.columns(2, gap="large")
            with category_col:
                st.markdown('<div class="wy-section-title">年度类别排行</div>', unsafe_allow_html=True)
                category_data = (
                    year_expenses.groupby("category")["amount"].sum()
                    .sort_values(ascending=True).reset_index()
                )
                if category_data.empty:
                    st.info("该年度没有支出。")
                else:
                    category_chart = px.bar(
                        category_data, x="amount", y="category", orientation="h",
                        labels={"amount": "支出 (RM)", "category": ""},
                        color_discrete_sequence=["#5B8FF9"]
                    )
                    category_chart.update_xaxes(rangemode="tozero", tickprefix="RM ")
                    category_chart.update_traces(hovertemplate="%{y}<br>支出 RM %{x:,.2f}<extra></extra>")
                    chart_layout(category_chart, height=max(360, 35 * len(category_data)))
                    st.plotly_chart(category_chart, use_container_width=True, config={"displayModeBar": False})
            with item_col:
                st.markdown('<div class="wy-section-title">高支出项目</div>', unsafe_allow_html=True)
                item_data = (
                    year_expenses.groupby("item")
                    .agg(总支出=("amount", "sum"), 次数=("amount", "size"))
                    .sort_values("总支出", ascending=False).head(15).reset_index()
                    .rename(columns={"item": "项目"})
                )
                st.dataframe(
                    item_data, hide_index=True, use_container_width=True, height=470,
                    column_config={
                        "项目": st.column_config.TextColumn(width="large"),
                        "总支出": st.column_config.NumberColumn(format="RM %.2f"),
                        "次数": st.column_config.NumberColumn(format="%d"),
                    },
                )


elif navigation == "AI 洞察":
    page_header("AI 洞察", "AI 负责解释趋势；金额统计仍由本地数据计算完成。")
    expense_years = (
        sorted(transactions.loc[transactions["type"] == EXPENSE, "date"].dt.year.unique().tolist(), reverse=True)
        if not transactions.empty else []
    )
    if not expense_years:
        st.markdown('<div class="wy-empty">暂无支出数据可分析</div>', unsafe_allow_html=True)
    else:
        ai_year = st.selectbox("分析年份", expense_years)
        year_expenses = transactions[
            (transactions["date"].dt.year == int(ai_year))
            & (transactions["type"] == EXPENSE)
        ].copy()
        classify_col, reset_col, ai_space = st.columns([1.2, 1, 3])
        if classify_col.button("AI 宏观归类", type="primary", use_container_width=True):
            unique_items = json.dumps(year_expenses["item"].unique().tolist(), ensure_ascii=False)
            with st.spinner("正在归类项目..."):
                mapping = categorize_macro(unique_items)
            if mapping:
                result = year_expenses.copy()
                result["宏观类别"] = result["item"].map(mapping).fillna("其他")
                st.session_state["macro_result"] = result
                st.session_state["macro_year"] = int(ai_year)
                st.rerun()
            else:
                st.error("AI 归类失败，请稍后重试。")
        if reset_col.button("清除分析", use_container_width=True):
            st.session_state.pop("macro_result", None)
            st.session_state.pop("macro_year", None)
            st.rerun()
        if st.session_state.get("macro_year") == int(ai_year):
            macro_result = st.session_state["macro_result"]
            macro_summary = (
                macro_result.groupby("宏观类别")["amount"]
                .sum().sort_values(ascending=True).reset_index()
            )
            macro_chart = px.bar(
                macro_summary, x="amount", y="宏观类别", orientation="h",
                labels={"amount": "支出 (RM)", "宏观类别": ""},
                color_discrete_sequence=["#5B8FF9"]
            )
            macro_chart.update_xaxes(rangemode="tozero", tickprefix="RM ")
            chart_layout(macro_chart, height=420)
            st.plotly_chart(macro_chart, use_container_width=True, config={"displayModeBar": False})

        st.divider()
        st.markdown('<div class="wy-section-title">与账单对话</div>', unsafe_allow_html=True)
        st.caption("只发送年度汇总、类别统计与最高金额记录，不发送完整账本。")
        if "ai_chat_history" not in st.session_state:
            st.session_state["ai_chat_history"] = []
        for message in st.session_state["ai_chat_history"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        question = st.chat_input("例如：哪一个月支出最高？主要原因是什么？")
        if question:
            st.session_state["ai_chat_history"].append({"role": "user", "content": question})
            summary = {
                "year": int(ai_year),
                "total_expense": round(float(year_expenses["amount"].sum()), 2),
                "monthly_expense": year_expenses.groupby(year_expenses["date"].dt.month)["amount"].sum().round(2).to_dict(),
                "category_expense": year_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).round(2).to_dict(),
                "largest_transactions": (
                    year_expenses.nlargest(15, "amount")[["date", "item", "category", "amount"]]
                    .astype({"date": str}).to_dict("records")
                ),
            }
            history_text = "\n".join(
                f"{message['role']}: {message['content']}"
                for message in st.session_state["ai_chat_history"][-6:]
            )
            prompt = (
                "你是私人财务分析助手。只根据提供的统计资料回答，不要编造交易。"
                "使用中文，金额使用 RM 并保留两位小数。"
                f"\n统计资料：{json.dumps(summary, ensure_ascii=False, default=str)}"
                f"\n最近对话：{history_text}\n当前问题：{question}"
            )
            try:
                with st.chat_message("assistant"):
                    with st.spinner("正在分析..."):
                        reply = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt).text
                        st.markdown(reply)
                st.session_state["ai_chat_history"].append({"role": "assistant", "content": reply})
            except Exception as exc:
                st.error(f"AI 回答失败：{exc}")


else:
    page_header("设置与备份", "类别新增已整合到交易流程；这里用于整理、合并与备份。")
    category_tab, backup_tab = st.tabs(["类别整理", "备份与导入"])
    with category_tab:
        st.info("新增类别可直接在“新增交易”或“编辑交易”的类别下拉选单中完成。")
        category_usage = (
            transactions.groupby("category").agg(交易笔数=("id", "size"), 总金额=("amount", "sum"))
            .reset_index().rename(columns={"category": "类别"})
            if not transactions.empty else pd.DataFrame(columns=["类别", "交易笔数", "总金额"])
        )
        st.dataframe(
            category_usage.sort_values("交易笔数", ascending=False),
            hide_index=True, use_container_width=True,
            column_config={
                "类别": st.column_config.TextColumn(width="large"),
                "交易笔数": st.column_config.NumberColumn(format="%d"),
                "总金额": st.column_config.NumberColumn(format="RM %.2f"),
            },
        )
        st.markdown('<div class="wy-section-title">合并或改名类别</div>', unsafe_allow_html=True)
        source_col, target_col = st.columns(2)
        source_category = source_col.selectbox("原类别", categories, key="merge_source")
        target_options = [category for category in categories if category != source_category] + [ADD_CATEGORY_OPTION]
        target_category = target_col.selectbox("目标类别", target_options, key="merge_target")
        if target_category == ADD_CATEGORY_OPTION:
            target_category = st.text_input("新类别名称", key="merge_new_target")
        affected_count = int((transactions["category"] == source_category).sum()) if not transactions.empty else 0
        st.caption(f"将更新 {affected_count} 笔旧交易；原类别随后会被删除。")
        merge_confirm = st.checkbox("我确认要移动旧交易并合并类别", key="merge_confirm")
        if st.button("执行合并", type="primary", disabled=not merge_confirm, use_container_width=True):
            if rename_or_merge_category(source_category, target_category):
                st.rerun()

    with backup_tab:
        if transactions.empty:
            st.info("暂无数据可导出。")
        else:
            export_frame = transactions.copy()
            export_frame["date"] = export_frame["date"].dt.date
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
                export_frame.to_excel(writer, index=False, sheet_name="Transactions")
            excel_col, csv_col, export_space = st.columns([1, 1, 3])
            excel_col.download_button(
                "下载 Excel", data=excel_buffer.getvalue(),
                file_name=f"WY_Wallet_V2_{date.today().isoformat()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary", use_container_width=True,
            )
            csv_col.download_button(
                "下载 CSV", data=export_frame.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"WY_Wallet_V2_{date.today().isoformat()}.csv",
                mime="text/csv", use_container_width=True,
            )
        st.divider()
        st.markdown('<div class="wy-section-title">导入交易</div>', unsafe_allow_html=True)
        st.warning("导入只会新增，不会覆盖或删除现有记录。")
        import_file = st.file_uploader("上传 CSV 或 Excel", type=["csv", "xlsx"], key="import_file")
        if import_file is not None:
            try:
                imported = pd.read_csv(import_file) if import_file.name.lower().endswith(".csv") else pd.read_excel(import_file)
                required = {"date", "item", "category", "type", "amount"}
                missing = required - set(imported.columns)
                if missing:
                    st.error("缺少栏位：" + "、".join(sorted(missing)))
                else:
                    if "note" not in imported.columns:
                        imported["note"] = ""
                    imported = imported[["date", "item", "category", "type", "amount", "note"]].copy()
                    existing_keys = set(zip(
                        transactions["date"].dt.strftime("%Y-%m-%d"),
                        transactions["item"].astype(str).str.strip(),
                        transactions["type"].astype(str),
                        transactions["amount"].round(2),
                    ))
                    import_dates = pd.to_datetime(imported["date"], errors="coerce")
                    imported["_duplicate"] = [
                        (
                            parsed_date.strftime("%Y-%m-%d") if not pd.isna(parsed_date) else "",
                            str(item).strip(), str(tx_type),
                            round(float(amount), 2) if pd.notna(amount) else None,
                        ) in existing_keys
                        for parsed_date, item, tx_type, amount in zip(
                            import_dates, imported["item"], imported["type"], imported["amount"]
                        )
                    ]
                    duplicate_count = int(imported["_duplicate"].sum())
                    preview = imported.drop(columns="_duplicate").head(100)
                    st.dataframe(preview, hide_index=True, use_container_width=True)
                    st.caption(f"共 {len(imported)} 笔，其中检测到 {duplicate_count} 笔可能重复。")
                    skip_duplicates = st.checkbox("自动跳过可能重复的交易", value=True)
                    ready = st.checkbox("确认导入这些交易")
                    to_import = (imported[~imported["_duplicate"]] if skip_duplicates else imported).drop(columns="_duplicate")
                    if st.button(
                        f"开始导入 {len(to_import)} 笔", type="primary",
                        disabled=not ready or to_import.empty, use_container_width=True,
                    ):
                        if insert_transactions(to_import):
                            st.rerun()
            except Exception as exc:
                st.error(f"读取文件失败：{exc}")
