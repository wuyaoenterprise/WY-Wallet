import calendar
import io
import json
from datetime import date, datetime

import google.generativeai as genai
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from PIL import Image
from supabase import create_client


APP_TITLE = "WY Wallet V2"
EXPENSE = "Expense"
INCOME = "Income"
TYPE_LABELS = {EXPENSE: "支出", INCOME: "收入"}
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
ADD_CATEGORY_OPTION = "＋ 新增类别"
MONTH_LABELS = [f"{month}月" for month in range(1, 13)]
CHART_CONFIG = {"displayModeBar": False, "responsive": True}

st.set_page_config(page_title=APP_TITLE, page_icon="💳", layout="wide")
st.markdown(
    """
    <style>
    :root {
        --wy-primary:#5b8ff9;
        --wy-positive:#35b77e;
        --wy-negative:#ef6464;
        --wy-warning:#f6bd16;
        --wy-border:rgba(128,128,128,.24);
        --wy-muted:rgba(160,166,180,.82);
    }
    [data-testid="stAppViewContainer"] > .main .block-container {
        max-width:1280px;padding-top:1.15rem;padding-bottom:3rem;
    }
    [data-testid="stSidebar"] {border-right:1px solid var(--wy-border)}
    .wy-brand{padding:.35rem 0 1rem}.wy-brand-title{font-size:1.45rem;font-weight:800;line-height:1.2}
    .wy-muted,.wy-brand-subtitle{color:var(--wy-muted);font-size:.88rem}
    .wy-page-title{font-size:1.9rem;font-weight:800;letter-spacing:-.03em;margin:0 0 .15rem}
    .wy-page-subtitle{color:var(--wy-muted);margin-bottom:1.15rem}
    .wy-section-title{font-size:1.05rem;font-weight:760;margin:.2rem 0 .65rem}
    .wy-card{border:1px solid var(--wy-border);border-radius:14px;padding:1rem;background:rgba(127,127,127,.025)}
    .wy-detail{border:1px solid var(--wy-border);border-radius:14px;padding:1rem 1.05rem;margin-top:.7rem;background:rgba(127,127,127,.025)}
    .wy-chip{display:inline-block;border:1px solid var(--wy-border);border-radius:999px;padding:.12rem .5rem;font-size:.78rem;color:var(--wy-muted)}
    .wy-empty{border:1px dashed var(--wy-border);border-radius:14px;padding:2rem;text-align:center;color:var(--wy-muted)}
    .wy-amount-expense{color:var(--wy-negative);font-weight:800}.wy-amount-income{color:var(--wy-positive);font-weight:800}
    div[data-testid="stMetric"]{border:1px solid var(--wy-border);border-radius:14px;padding:.82rem 1rem;background:rgba(127,127,127,.035)}
    div[data-testid="stMetricLabel"]{color:var(--wy-muted)} div[data-testid="stMetricValue"]{font-size:1.42rem}
    .wy-calendar{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}
    .wy-calendar-head{text-align:center;color:var(--wy-muted);font-size:.78rem;padding:.25rem}
    .wy-calendar-day{min-height:68px;border:1px solid var(--wy-border);border-radius:10px;padding:.45rem}
    .wy-calendar-date{color:var(--wy-muted);font-size:.78rem}.wy-calendar-amount{font-size:.84rem;font-weight:750;margin-top:.42rem}
    .wy-callout{border-left:3px solid var(--wy-primary);padding:.7rem .9rem;background:rgba(91,143,249,.08);border-radius:0 10px 10px 0;margin:.4rem 0}
    @media(max-width:760px){
        [data-testid="stAppViewContainer"] > .main .block-container{padding-left:.7rem;padding-right:.7rem}
        .wy-page-title{font-size:1.55rem}.wy-calendar{gap:3px}.wy-calendar-day{min-height:48px;padding:.25rem}
        .wy-calendar-amount{font-size:.66rem;margin-top:.2rem}
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
        defaults = {"item": "未知", "category": "其他", "type": EXPENSE, "amount": 0.0, "note": ""}
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
        values = [str(row.get("name", "")).strip() for row in response.data]
        values = [value for value in values if value]
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
        item = str(row.get("item") or "未知").strip()
        category = str(row.get("category") or "其他").strip()
        normalized.append({
            "date": parsed_date.date().isoformat(),
            "item": item or "未知",
            "category": category or "其他",
            "type": INCOME if str(row.get("type")) == INCOME else EXPENSE,
            "amount": float(amount),
            "note": str(row.get("note") or "").strip(),
        })
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
    if not cleaned:
        st.warning("请输入类别名称。")
        return False
    existing = {category.casefold() for category in load_categories()}
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
    source, target = str(source).strip(), str(target).strip()
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
        base["收入"], base["支出"] = 0.0, 0.0
    else:
        grouped = (
            year_data.assign(month=year_data["date"].dt.month)
            .pivot_table(index="month", columns="type", values="amount", aggfunc="sum", fill_value=0)
            .reset_index()
        )
        for column in [INCOME, EXPENSE]:
            if column not in grouped.columns:
                grouped[column] = 0.0
        grouped = grouped.rename(columns={INCOME: "收入", EXPENSE: "支出"})
        base = base.merge(grouped[["month", "收入", "支出"]], on="month", how="left").fillna(0)
    base["结余"] = base["收入"] - base["支出"]
    base["储蓄率"] = base.apply(lambda row: (row["结余"] / row["收入"] * 100) if row["收入"] > 0 else 0.0, axis=1)
    base["累计支出"] = base["支出"].cumsum()
    return base


def recent_months_summary(frame: pd.DataFrame, periods: int = 12) -> pd.DataFrame:
    now = pd.Period(datetime.now(), freq="M")
    period_index = pd.period_range(end=now, periods=periods, freq="M")
    base = pd.DataFrame({"period": period_index, "月份": [period.strftime("%Y-%m") for period in period_index]})
    expenses = frame[frame["type"] == EXPENSE].copy()
    if expenses.empty:
        base["支出"] = 0.0
    else:
        expenses["period"] = expenses["date"].dt.to_period("M")
        grouped = expenses.groupby("period")["amount"].sum().reset_index(name="支出")
        base = base.merge(grouped, on="period", how="left")
        base["支出"] = base["支出"].fillna(0.0)
    return base


def previous_month(year: int, month_number: int) -> tuple[int, int]:
    return (year - 1, 12) if month_number == 1 else (year, month_number - 1)


def chart_layout(fig, height: int = 360, show_legend: bool = False, hovermode: str = "x unified"):
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=30, b=8),
        legend_title_text="",
        showlegend=show_legend,
        hovermode=hovermode,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
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
        餐饮美食、交通出行、居家生活、购物消费、休闲娱乐、医疗健康、教育学习、投资理财、旅游度假、其他。
        只返回 JSON 对象，例如 {{"KFC":"餐饮美食"}}。
        输入：{items_json}
        """
        response = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt)
        result = clean_json(response.text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def build_calendar_html(expenses: pd.DataFrame, year: int, month_number: int) -> str:
    daily_sums = expenses.groupby(expenses["date"].dt.day)["amount"].sum().to_dict() if not expenses.empty else {}
    headers = "".join(f'<div class="wy-calendar-head">{name}</div>' for name in ["一", "二", "三", "四", "五", "六", "日"])
    cells = []
    for week in calendar.monthcalendar(int(year), int(month_number)):
        for day_number in week:
            if day_number == 0:
                cells.append('<div class="wy-calendar-day" style="opacity:.2"></div>')
            else:
                value = float(daily_sums.get(day_number, 0.0))
                amount_html = f'<div class="wy-calendar-amount">{money(value)}</div>' if value else ""
                cells.append(f'<div class="wy-calendar-day"><div class="wy-calendar-date">{day_number}</div>{amount_html}</div>')
    return f'<div class="wy-calendar">{headers}{"".join(cells)}</div>'


def quick_insight_text(year_data: pd.DataFrame, annual: pd.DataFrame) -> list[str]:
    insights = []
    if year_data.empty:
        return ["该年度没有支出记录。"]
    total = float(year_data["amount"].sum())
    by_category = year_data.groupby("category")["amount"].sum().sort_values(ascending=False)
    if not by_category.empty and total > 0:
        insights.append(f"最大类别是 {by_category.index[0]}，占全年支出的 {by_category.iloc[0] / total:.1%}。")
    nonzero = annual[annual["支出"] > 0]
    if not nonzero.empty:
        high = nonzero.loc[nonzero["支出"].idxmax()]
        low = nonzero.loc[nonzero["支出"].idxmin()]
        insights.append(f"支出最高为 {high['月份']} {money(high['支出'])}；最低为 {low['月份']} {money(low['支出'])}。")
    top10 = float(year_data.nlargest(min(10, len(year_data)), "amount")["amount"].sum())
    if total > 0:
        insights.append(f"最高的 10 笔交易占全年支出的 {top10 / total:.1%}。")
    return insights


def anomaly_transactions(expenses: pd.DataFrame) -> pd.DataFrame:
    if len(expenses) < 5:
        return pd.DataFrame(columns=expenses.columns)
    q1, q3 = expenses["amount"].quantile([0.25, 0.75])
    iqr = q3 - q1
    threshold = q3 + 1.5 * iqr
    if iqr == 0:
        threshold = float(expenses["amount"].mean() + 2 * expenses["amount"].std(ddof=0))
    return expenses[expenses["amount"] > max(threshold, 0)].sort_values("amount", ascending=False)


def recurring_items(expenses: pd.DataFrame) -> pd.DataFrame:
    if expenses.empty:
        return pd.DataFrame(columns=["项目", "次数", "总支出", "平均每笔", "最近日期"])
    grouped = (
        expenses.assign(_key=expenses["item"].str.strip().str.casefold())
        .groupby("_key")
        .agg(项目=("item", "first"), 次数=("amount", "size"), 总支出=("amount", "sum"), 平均每笔=("amount", "mean"), 最近日期=("date", "max"))
        .reset_index(drop=True)
    )
    return grouped[grouped["次数"] >= 3].sort_values(["次数", "总支出"], ascending=False)


@st.dialog("新增交易", width="large")
def add_transaction_dialog(categories: list[str]) -> None:
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=date.today(), key="add_date")
    tx_type = col_type.segmented_control("类型", options=[EXPENSE, INCOME], default=EXPENSE, format_func=lambda value: TYPE_LABELS[value], key="add_type")
    options = categories + [ADD_CATEGORY_OPTION]
    selected_category = st.selectbox("类别", options, key="add_category")
    new_category_name = ""
    if selected_category == ADD_CATEGORY_OPTION:
        new_category_name = st.text_input("新类别名称", placeholder="保存交易时会同时建立", key="add_new_category")
    item = st.text_input("项目或商家", placeholder="例如：午餐、Grab、房租", key="add_item")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="0.00", key="add_amount")
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
                ready = selected_category != ADD_CATEGORY_OPTION or create_category(effective_category)
                if ready and insert_transactions([{"date": tx_date, "item": item, "category": effective_category, "type": tx_type or EXPENSE, "amount": amount, "note": note}]):
                    st.rerun()


@st.dialog("编辑交易", width="large")
def edit_transaction_dialog(transaction_id: int, categories: list[str]) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易，它可能已被删除。")
        return
    row = match.iloc[0]
    col_date, col_type = st.columns(2)
    tx_date = col_date.date_input("日期", value=row["date"].date(), key=f"edit_date_{transaction_id}")
    tx_type = col_type.segmented_control("类型", options=[EXPENSE, INCOME], default=row["type"], format_func=lambda value: TYPE_LABELS[value], key=f"edit_type_{transaction_id}")
    options = categories.copy()
    if row["category"] not in options:
        options.insert(0, row["category"])
    options.append(ADD_CATEGORY_OPTION)
    selected_category = st.selectbox("类别", options, index=options.index(row["category"]), key=f"edit_category_{transaction_id}")
    new_category_name = ""
    if selected_category == ADD_CATEGORY_OPTION:
        new_category_name = st.text_input("新类别名称", key=f"edit_new_category_{transaction_id}")
    item = st.text_input("项目或商家", value=row["item"], key=f"edit_item_{transaction_id}")
    amount = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=float(row["amount"]), key=f"edit_amount_{transaction_id}")
    note = st.text_area("备注", value=row["note"], key=f"edit_note_{transaction_id}")
    if st.button("保存修改", type="primary", use_container_width=True):
        effective_category = new_category_name.strip() if selected_category == ADD_CATEGORY_OPTION else selected_category
        if not item.strip() or amount <= 0 or not effective_category:
            st.warning("请完整填写项目、类别和有效金额。")
        else:
            ready = selected_category != ADD_CATEGORY_OPTION or create_category(effective_category)
            if ready and update_transaction(transaction_id, {"date": tx_date, "item": item, "category": effective_category, "type": tx_type or EXPENSE, "amount": amount, "note": note}):
                st.rerun()


@st.dialog("删除交易")
def delete_transaction_dialog(transaction_id: int) -> None:
    current = load_transactions()
    match = current[current["id"] == int(transaction_id)]
    if match.empty:
        st.error("找不到这笔交易。")
        return
    row = match.iloc[0].to_dict()
    st.warning("删除后会从 Supabase 移除，本次浏览期间可撤销一次。")
    st.write(f"**{row['item']}**")
    st.caption(f"{row['date'].date()} · {row['category']} · {money(row['amount'])}")
    confirm = st.checkbox("我确认删除这笔交易", key=f"confirm_delete_{transaction_id}")
    if st.button("确认删除", type="primary", disabled=not confirm, use_container_width=True, key=f"delete_{transaction_id}"):
        if delete_transaction(row):
            st.rerun()


transactions = load_transactions()
categories = load_categories()
usage_counts = transactions["category"].value_counts().to_dict() if not transactions.empty else {}
categories = sorted(categories, key=lambda value: (-usage_counts.get(value, 0), value))

if st.session_state.get("database_error"):
    st.error("数据库读取失败：" + st.session_state["database_error"])

with st.sidebar:
    st.markdown('<div class="wy-brand"><div class="wy-brand-title">💳 WY Wallet</div><div class="wy-brand-subtitle">个人财务中心 · V2</div></div>', unsafe_allow_html=True)
    if st.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    navigation = st.radio("导航", options=["总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"], format_func=lambda value: {"总览": "⌂  总览", "交易记录": "≡  交易记录", "分析报表": "▥  分析报表", "AI 洞察": "✦  AI 洞察", "设置与备份": "⚙  设置与备份"}[value], label_visibility="collapsed")
    st.divider()
    st.caption("V2 独立部署")
    st.caption("继续使用现有 Supabase 数据")


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="wy-page-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="wy-page-subtitle">{subtitle}</div>', unsafe_allow_html=True)


if navigation == "总览":
    page_header("财务总览", "用最少时间了解本月状况、近期趋势和主要支出。")
    now = datetime.now()
    current_month = month_slice(transactions, now.year, now.month)
    prior_year, prior_month_number = previous_month(now.year, now.month)
    prior_month = month_slice(transactions, prior_year, prior_month_number)
    income, expense, balance = calculate_totals(current_month)
    _, prior_expense, _ = calculate_totals(prior_month)
    change = None if prior_expense == 0 else (expense - prior_expense) / prior_expense
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected = expense / max(now.day, 1) * days_in_month
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("本月收入", money(income))
    m2.metric("本月支出", money(expense), "无上月数据" if change is None else f"{change:+.1%} 对比上月", delta_color="inverse")
    m3.metric("本月结余", money(balance))
    m4.metric("日均支出", money(expense / max(now.day, 1)))
    m5.metric("月底预计", money(projected), "按当前速度", delta_color="off")
    left, right = st.columns([1.65, 1], gap="large")
    with left:
        st.markdown('<div class="wy-section-title">最近 12 个月支出</div>', unsafe_allow_html=True)
        twelve = recent_months_summary(transactions, 12)
        fig = px.bar(twelve, x="月份", y="支出", text_auto=".0f", color_discrete_sequence=["#5B8FF9"])
        fig.update_yaxes(rangemode="tozero", tickprefix="RM ")
        fig.update_traces(hovertemplate="%{x}<br>支出 RM %{y:,.2f}<extra></extra>")
        chart_layout(fig, 335)
        st.plotly_chart(fig, use_container_width=True, config=CHART_CONFIG)
    with right:
        st.markdown('<div class="wy-section-title">本月支出类别</div>', unsafe_allow_html=True)
        month_expenses = current_month[current_month["type"] == EXPENSE]
        category_summary = month_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).head(7)
        if category_summary.empty:
            st.markdown('<div class="wy-empty">本月暂无支出</div>', unsafe_allow_html=True)
        else:
            for category_name, value in category_summary.items():
                label_col, value_col = st.columns([1.6, 1])
                label_col.write(f"**{category_name}**")
                value_col.write(f"<div style='text-align:right'>{money(value)}</div>", unsafe_allow_html=True)
                st.progress(min(float(value / expense), 1.0) if expense else 0.0)
    st.markdown('<div class="wy-section-title">最近交易</div>', unsafe_allow_html=True)
    if transactions.empty:
        st.markdown('<div class="wy-empty">还没有任何交易记录</div>', unsafe_allow_html=True)
    else:
        recent = transactions.head(8).copy()
        recent["日期"] = recent["date"].dt.strftime("%Y-%m-%d")
        recent["项目"] = recent["item"]
        recent["类别"] = recent["category"]
        recent["类型"] = recent["type"].map(TYPE_LABELS)
        recent["金额"] = recent.apply(lambda row: row["amount"] if row["type"] == INCOME else -row["amount"], axis=1)
        st.dataframe(recent[["日期", "项目", "类别", "类型", "金额"]], hide_index=True, use_container_width=True, height=315, column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})


elif navigation == "交易记录":
    page_header("交易记录", "统一搜索、筛选、检查与编辑所有账目。")
    action_add, action_receipt, action_undo, _ = st.columns([1, 1.15, 1.25, 3.6])
    if action_add.button("＋ 新增交易", type="primary", use_container_width=True):
        add_transaction_dialog(categories)
    show_receipt = action_receipt.toggle("收据识别", key="show_receipt")
    if st.session_state.get("recently_deleted") and action_undo.button("↩ 撤销最近删除", use_container_width=True):
        if insert_transactions([st.session_state["recently_deleted"]]):
            del st.session_state["recently_deleted"]
            st.rerun()
    if show_receipt:
        with st.container(border=True):
            st.markdown('<div class="wy-section-title">AI 收据识别</div>', unsafe_allow_html=True)
            uploaded = st.file_uploader("上传 JPG、JPEG 或 PNG", type=["jpg", "jpeg", "png"], key="receipt_upload")
            if uploaded is not None:
                preview, action = st.columns([1, 1.5])
                preview.image(uploaded, use_container_width=True)
                if action.button("开始识别", type="primary", use_container_width=True):
                    with st.spinner("正在识别收据项目..."):
                        result, error = analyze_receipt(Image.open(uploaded))
                    if error:
                        st.error(error)
                    else:
                        st.session_state["pending_receipt"] = result
                        st.rerun()
            if "pending_receipt" in st.session_state:
                pending = pd.DataFrame(st.session_state["pending_receipt"])
                defaults = {"date": date.today(), "item": "", "category": "其他", "type": EXPENSE, "amount": 0.0, "note": ""}
                for column, default in defaults.items():
                    if column not in pending.columns:
                        pending[column] = default
                pending["date"] = pd.to_datetime(pending["date"], errors="coerce").fillna(pd.Timestamp(date.today()))
                edited = st.data_editor(pending[["date", "item", "category", "type", "amount", "note"]], num_rows="dynamic", hide_index=True, use_container_width=True, column_config={"date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"), "item": st.column_config.TextColumn("项目", required=True), "category": st.column_config.SelectboxColumn("类别", options=categories, required=True), "type": st.column_config.SelectboxColumn("类型", options=[EXPENSE, INCOME], required=True), "amount": st.column_config.NumberColumn("金额 (RM)", min_value=0.0, format="%.2f", required=True), "note": st.column_config.TextColumn("备注")})
                save_col, discard_col, new_cat_col, _ = st.columns([1, 1, 1.2, 2])
                if save_col.button("确认保存", type="primary", use_container_width=True):
                    if insert_transactions(edited):
                        del st.session_state["pending_receipt"]
                        st.rerun()
                if discard_col.button("放弃识别", use_container_width=True):
                    del st.session_state["pending_receipt"]
                    st.rerun()
                with new_cat_col.popover("＋ 新增类别", use_container_width=True):
                    name = st.text_input("类别名称", key="receipt_category_name")
                    if st.button("建立类别", key="receipt_category_create", use_container_width=True) and create_category(name):
                        st.rerun()
    with st.expander("筛选交易", expanded=True):
        search_col, year_col, month_col = st.columns([2, 1, 1])
        search_text = search_col.text_input("搜索", placeholder="项目、类别或备注")
        years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
        selected_year = year_col.selectbox("年份", ["全部"] + years)
        selected_month = month_col.selectbox("月份", ["全部"] + list(range(1, 13)))
        type_col, category_col, sort_col = st.columns([1, 1.5, 1.5])
        selected_type = type_col.selectbox("类型", ["全部", EXPENSE, INCOME], format_func=lambda x: "全部" if x == "全部" else TYPE_LABELS[x])
        selected_category = category_col.selectbox("类别", ["全部"] + categories)
        selected_sort = sort_col.selectbox("排序", ["日期：最新优先", "日期：最早优先", "金额：由高到低", "金额：由低到高"])
    filtered = transactions.copy()
    if search_text:
        mask = filtered["item"].str.contains(search_text, case=False, na=False) | filtered["category"].str.contains(search_text, case=False, na=False) | filtered["note"].str.contains(search_text, case=False, na=False)
        filtered = filtered[mask]
    if selected_year != "全部": filtered = filtered[filtered["date"].dt.year == int(selected_year)]
    if selected_month != "全部": filtered = filtered[filtered["date"].dt.month == int(selected_month)]
    if selected_type != "全部": filtered = filtered[filtered["type"] == selected_type]
    if selected_category != "全部": filtered = filtered[filtered["category"] == selected_category]
    sort_rules = {"日期：最新优先": ("date", False), "日期：最早优先": ("date", True), "金额：由高到低": ("amount", False), "金额：由低到高": ("amount", True)}
    sort_column, ascending = sort_rules[selected_sort]
    filtered = filtered.sort_values([sort_column, "id"], ascending=[ascending, ascending])
    fi, fe, fb = calculate_totals(filtered)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("筛选结果", f"{len(filtered):,} 笔"); c2.metric("支出", money(fe)); c3.metric("收入", money(fi)); c4.metric("净额", money(fb))
    st.caption("点击一行，可在下方查看、编辑或删除。")
    table = filtered.copy()
    table["日期"] = table["date"].dt.strftime("%Y-%m-%d"); table["项目"] = table["item"]; table["类别"] = table["category"]; table["类型"] = table["type"].map(TYPE_LABELS); table["金额"] = table.apply(lambda row: row["amount"] if row["type"] == INCOME else -row["amount"], axis=1); table["备注"] = table["note"]; table["_id"] = table["id"]
    event = st.dataframe(table[["_id", "日期", "项目", "类别", "类型", "金额", "备注"]], column_order=["日期", "项目", "类别", "类型", "金额", "备注"], hide_index=True, use_container_width=True, height=560, on_select="rerun", selection_mode="single-row", key="transaction_table", column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})
    selected_rows = event.selection.rows
    if selected_rows and selected_rows[0] < len(table):
        selected = table.iloc[selected_rows[0]]
        original = filtered[filtered["id"] == selected["_id"]].iloc[0]
        amount_class = "wy-amount-income" if original["type"] == INCOME else "wy-amount-expense"
        sign = "+" if original["type"] == INCOME else "−"
        note_suffix = f" · {original['note']}" if original["note"] else ""
        st.markdown(f'<div class="wy-detail"><span class="wy-chip">{TYPE_LABELS[original["type"]]}</span> <span class="wy-chip">{original["category"]}</span><h3 style="margin:.55rem 0 .2rem">{original["item"]}</h3><div class="{amount_class}" style="font-size:1.35rem">{sign}{money(original["amount"])}</div><div class="wy-muted" style="margin-top:.35rem">{original["date"].date()}{note_suffix}</div></div>', unsafe_allow_html=True)
        edit_col, delete_col, _ = st.columns([1, 1, 4])
        if edit_col.button("编辑交易", type="primary", use_container_width=True): edit_transaction_dialog(int(original["id"]), categories)
        if delete_col.button("删除交易", use_container_width=True): delete_transaction_dialog(int(original["id"]))


elif navigation == "分析报表":
    page_header("分析报表", "保留快速图表，同时增加年度、月度、类别和异常消费分析。")
    if transactions.empty:
        st.markdown('<div class="wy-empty">暂无数据可分析</div>', unsafe_allow_html=True)
    else:
        available_years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
        default_index = available_years.index(datetime.now().year) if datetime.now().year in available_years else 0
        report_year = st.selectbox("分析年份", available_years, index=default_index)
        annual = monthly_summary(transactions, int(report_year))
        year_all = transactions[transactions["date"].dt.year == int(report_year)].copy()
        year_expenses = year_all[year_all["type"] == EXPENSE].copy()
        annual_expense = float(annual["支出"].sum()); annual_income = float(annual["收入"].sum())
        active_months = max(int((annual["支出"] > 0).sum()), 1); average_expense = annual_expense / active_months
        highest_month = annual.loc[annual["支出"].idxmax()]
        prior_annual = monthly_summary(transactions, int(report_year) - 1); prior_total = float(prior_annual["支出"].sum())
        year_change = None if prior_total == 0 else (annual_expense - prior_total) / prior_total
        savings_rate = (annual_income - annual_expense) / annual_income * 100 if annual_income > 0 else 0.0
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("年度支出", money(annual_expense), "无去年数据" if year_change is None else f"{year_change:+.1%} 同比", delta_color="inverse")
        m2.metric("年度收入", money(annual_income)); m3.metric("月均支出", money(average_expense)); m4.metric("储蓄率", f"{savings_rate:.1f}%"); m5.metric("最高月份", str(highest_month["月份"]), money(highest_month["支出"]), delta_color="off")
        quick_tab, annual_tab, monthly_tab, category_tab, insight_tab = st.tabs(["快速总览", "年度趋势", "月度明细", "类别分析", "异常与规律"])
        with quick_tab:
            st.caption("保留你原本习惯的图：12 个月支出、分类占比、每日柱状图和月历。")
            q_left, q_right = st.columns([1.55, 1], gap="large")
            with q_left:
                st.markdown('<div class="wy-section-title">全年每月支出</div>', unsafe_allow_html=True)
                bar = px.bar(annual, x="月份", y="支出", text_auto=".0f", color_discrete_sequence=["#5B8FF9"])
                bar.add_hline(y=average_expense, line_dash="dash", line_color="#F6BD16", annotation_text=f"月均 {money(average_expense)}")
                bar.update_yaxes(rangemode="tozero", tickprefix="RM "); bar.update_traces(hovertemplate="%{x}<br>支出 RM %{y:,.2f}<extra></extra>")
                chart_layout(bar, 390); st.plotly_chart(bar, use_container_width=True, config=CHART_CONFIG)
            with q_right:
                st.markdown('<div class="wy-section-title">年度类别占比</div>', unsafe_allow_html=True)
                cat = year_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).reset_index()
                if cat.empty:
                    st.info("没有支出数据。")
                else:
                    top = cat.head(7).copy()
                    if len(cat) > 7: top = pd.concat([top, pd.DataFrame({"category": ["其他类别"], "amount": [cat.iloc[7:]["amount"].sum()]})], ignore_index=True)
                    donut = px.pie(top, values="amount", names="category", hole=.56)
                    donut.update_traces(textposition="inside", textinfo="percent", hovertemplate="%{label}<br>RM %{value:,.2f}<br>%{percent}<extra></extra>")
                    chart_layout(donut, 390, True, "closest"); st.plotly_chart(donut, use_container_width=True, config=CHART_CONFIG)
            month_col, _ = st.columns([1, 3])
            quick_month = month_col.selectbox("快速查看月份", range(1, 13), index=datetime.now().month - 1, format_func=lambda value: f"{value}月", key="quick_month")
            selected = month_slice(transactions, int(report_year), int(quick_month)); selected_expenses = selected[selected["type"] == EXPENSE].copy()
            days = calendar.monthrange(int(report_year), int(quick_month))[1]; daily = pd.DataFrame({"day": range(1, days + 1)})
            if not selected_expenses.empty:
                grouped = selected_expenses.assign(day=selected_expenses["date"].dt.day).groupby("day")["amount"].sum().reset_index(); daily = daily.merge(grouped, on="day", how="left")
            if "amount" not in daily.columns: daily["amount"] = 0.0
            else: daily["amount"] = daily["amount"].fillna(0.0)
            qd_left, qd_right = st.columns([1.45, 1], gap="large")
            with qd_left:
                st.markdown('<div class="wy-section-title">每日支出</div>', unsafe_allow_html=True)
                dbar = px.bar(daily, x="day", y="amount", labels={"day": "日期", "amount": "支出 (RM)"}, color_discrete_sequence=["#5B8FF9"])
                dbar.update_xaxes(dtick=1); dbar.update_yaxes(rangemode="tozero", tickprefix="RM "); dbar.update_traces(hovertemplate=f"{quick_month}月 %{{x}}日<br>RM %{{y:,.2f}}<extra></extra>")
                chart_layout(dbar, 355); st.plotly_chart(dbar, use_container_width=True, config=CHART_CONFIG)
            with qd_right:
                st.markdown('<div class="wy-section-title">当月类别排行</div>', unsafe_allow_html=True)
                month_cat = selected_expenses.groupby("category")["amount"].sum().sort_values().reset_index()
                if month_cat.empty: st.info("该月没有支出。")
                else:
                    hbar = px.bar(month_cat, x="amount", y="category", orientation="h", labels={"amount": "支出 (RM)", "category": ""}, color_discrete_sequence=["#5B8FF9"])
                    hbar.update_xaxes(rangemode="tozero", tickprefix="RM "); chart_layout(hbar, 355, False, "closest"); st.plotly_chart(hbar, use_container_width=True, config=CHART_CONFIG)
            with st.expander("查看月历", expanded=False): st.markdown(build_calendar_html(selected_expenses, int(report_year), int(quick_month)), unsafe_allow_html=True)
        with annual_tab:
            st.markdown('<div class="wy-section-title">收入、支出与结余</div>', unsafe_allow_html=True)
            cash = annual.melt(id_vars=["month", "月份"], value_vars=["收入", "支出", "结余"], var_name="指标", value_name="金额")
            cash_chart = px.line(cash, x="月份", y="金额", color="指标", markers=True, color_discrete_map={"收入": "#35B77E", "支出": "#EF6464", "结余": "#F6BD16"})
            cash_chart.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(cash_chart, 390, True); st.plotly_chart(cash_chart, use_container_width=True, config=CHART_CONFIG)
            a_left, a_right = st.columns(2, gap="large")
            with a_left:
                st.markdown('<div class="wy-section-title">累计支出：今年 vs 去年</div>', unsafe_allow_html=True)
                compare = annual[["月份", "累计支出"]].copy().rename(columns={"累计支出": str(report_year)}); compare[str(int(report_year) - 1)] = prior_annual["累计支出"].values
                compare_melt = compare.melt(id_vars="月份", var_name="年份", value_name="累计支出")
                cumulative_chart = px.line(compare_melt, x="月份", y="累计支出", color="年份", markers=True); cumulative_chart.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(cumulative_chart, 350, True); st.plotly_chart(cumulative_chart, use_container_width=True, config=CHART_CONFIG)
            with a_right:
                st.markdown('<div class="wy-section-title">每月储蓄率</div>', unsafe_allow_html=True)
                saving_chart = px.bar(annual, x="月份", y="储蓄率", text_auto=".0f", color_discrete_sequence=["#35B77E"]); saving_chart.add_hline(y=0, line_color="#EF6464"); saving_chart.update_yaxes(ticksuffix="%"); saving_chart.update_traces(hovertemplate="%{x}<br>储蓄率 %{y:.1f}%<extra></extra>"); chart_layout(saving_chart, 350); st.plotly_chart(saving_chart, use_container_width=True, config=CHART_CONFIG)
            st.markdown('<div class="wy-section-title">类别随月份变化</div>', unsafe_allow_html=True)
            if year_expenses.empty: st.info("没有支出数据。")
            else:
                stacked = year_expenses.assign(月份=year_expenses["date"].dt.month.map(lambda x: f"{x}月")).groupby(["月份", "category"])["amount"].sum().reset_index(); stacked["月份"] = pd.Categorical(stacked["月份"], categories=MONTH_LABELS, ordered=True); stacked = stacked.sort_values("月份")
                stacked_chart = px.bar(stacked, x="月份", y="amount", color="category", labels={"amount": "支出 (RM)", "category": "类别"}); stacked_chart.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(stacked_chart, 430, True); st.plotly_chart(stacked_chart, use_container_width=True, config=CHART_CONFIG)
        with monthly_tab:
            selector, _ = st.columns([1, 3]); report_month = selector.selectbox("选择月份", range(1, 13), index=datetime.now().month - 1, format_func=lambda value: f"{value}月", key="report_month")
            selected = month_slice(transactions, int(report_year), int(report_month)); mi, me, mb = calculate_totals(selected); expense_rows = selected[selected["type"] == EXPENSE].copy(); days = calendar.monthrange(int(report_year), int(report_month))[1]; elapsed = datetime.now().day if int(report_year) == datetime.now().year and int(report_month) == datetime.now().month else days; projected_month = me / max(elapsed, 1) * days
            mm1, mm2, mm3, mm4, mm5 = st.columns(5); mm1.metric("收入", money(mi)); mm2.metric("支出", money(me)); mm3.metric("结余", money(mb)); mm4.metric("交易笔数", f"{len(selected):,}"); mm5.metric("月底预计", money(projected_month), "按当前速度", delta_color="off")
            if expense_rows.empty: st.info("该月没有支出。")
            else:
                expense_rows["day"] = expense_rows["date"].dt.day; daily_stacked = expense_rows.groupby(["day", "category"])["amount"].sum().reset_index()
                st.markdown('<div class="wy-section-title">每日支出及类别组成</div>', unsafe_allow_html=True)
                daily_stacked_chart = px.bar(daily_stacked, x="day", y="amount", color="category", labels={"day": "日期", "amount": "支出 (RM)", "category": "类别"}); daily_stacked_chart.update_xaxes(dtick=1); daily_stacked_chart.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(daily_stacked_chart, 420, True); st.plotly_chart(daily_stacked_chart, use_container_width=True, config=CHART_CONFIG)
                md_left, md_right = st.columns(2, gap="large")
                with md_left:
                    st.markdown('<div class="wy-section-title">星期几最容易花钱</div>', unsafe_allow_html=True)
                    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]; weekday = expense_rows.assign(weekday=expense_rows["date"].dt.dayofweek).groupby("weekday")["amount"].sum().reindex(range(7), fill_value=0).reset_index(); weekday["星期"] = weekday["weekday"].map(dict(enumerate(weekday_names)))
                    weekday_chart = px.bar(weekday, x="星期", y="amount", labels={"amount": "支出 (RM)"}, color_discrete_sequence=["#5B8FF9"]); weekday_chart.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(weekday_chart, 350); st.plotly_chart(weekday_chart, use_container_width=True, config=CHART_CONFIG)
                with md_right:
                    st.markdown('<div class="wy-section-title">单笔金额分布</div>', unsafe_allow_html=True)
                    histogram = px.histogram(expense_rows, x="amount", nbins=min(16, max(5, len(expense_rows) // 3)), labels={"amount": "单笔金额 (RM)", "count": "笔数"}, color_discrete_sequence=["#5B8FF9"]); histogram.update_xaxes(rangemode="tozero", tickprefix="RM "); chart_layout(histogram, 350, False, "closest"); st.plotly_chart(histogram, use_container_width=True, config=CHART_CONFIG)
                with st.expander("查看月历"): st.markdown(build_calendar_html(expense_rows, int(report_year), int(report_month)), unsafe_allow_html=True)
        with category_tab:
            if year_expenses.empty: st.info("该年度没有支出。")
            else:
                category_data = year_expenses.groupby("category")["amount"].agg(["sum", "size", "mean"]).reset_index().rename(columns={"sum": "总支出", "size": "次数", "mean": "平均每笔"}).sort_values("总支出", ascending=False)
                c_left, c_right = st.columns([1.25, 1], gap="large")
                with c_left:
                    st.markdown('<div class="wy-section-title">类别金额排行</div>', unsafe_allow_html=True); hdata = category_data.sort_values("总支出"); hchart = px.bar(hdata, x="总支出", y="category", orientation="h", labels={"category": "", "总支出": "支出 (RM)"}, color_discrete_sequence=["#5B8FF9"]); hchart.update_xaxes(rangemode="tozero", tickprefix="RM "); chart_layout(hchart, max(390, 34 * len(hdata)), False, "closest"); st.plotly_chart(hchart, use_container_width=True, config=CHART_CONFIG)
                with c_right:
                    st.markdown('<div class="wy-section-title">类别详细指标</div>', unsafe_allow_html=True); st.dataframe(category_data.rename(columns={"category": "类别"}), hide_index=True, use_container_width=True, height=430, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
                selected_category = st.selectbox("深入查看类别", category_data["category"].tolist()); category_rows = year_expenses[year_expenses["category"] == selected_category].copy(); category_monthly = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS}); grouped = category_rows.assign(month=category_rows["date"].dt.month).groupby("month")["amount"].sum().reset_index(name="支出"); category_monthly = category_monthly.merge(grouped, on="month", how="left").fillna(0)
                cat_left, cat_right = st.columns([1.4, 1], gap="large")
                with cat_left:
                    st.markdown(f'<div class="wy-section-title">{selected_category} 的 12 个月趋势</div>', unsafe_allow_html=True); line = px.line(category_monthly, x="月份", y="支出", markers=True, color_discrete_sequence=["#5B8FF9"]); line.update_yaxes(rangemode="tozero", tickprefix="RM "); chart_layout(line, 340); st.plotly_chart(line, use_container_width=True, config=CHART_CONFIG)
                with cat_right:
                    st.markdown('<div class="wy-section-title">该类别项目排行</div>', unsafe_allow_html=True); item_rank = category_rows.groupby("item")["amount"].agg(["sum", "size"]).reset_index().rename(columns={"item": "项目", "sum": "总支出", "size": "次数"}).sort_values("总支出", ascending=False).head(12); st.dataframe(item_rank, hide_index=True, use_container_width=True, height=340, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f")})
        with insight_tab:
            st.markdown('<div class="wy-section-title">快速结论</div>', unsafe_allow_html=True)
            for insight in quick_insight_text(year_expenses, annual): st.markdown(f'<div class="wy-callout">{insight}</div>', unsafe_allow_html=True)
            anomalies = anomaly_transactions(year_expenses); recurrences = recurring_items(year_expenses); i_left, i_right = st.columns(2, gap="large")
            with i_left:
                st.markdown('<div class="wy-section-title">异常高额交易</div>', unsafe_allow_html=True); st.caption("使用四分位距自动寻找明显高于平常的交易；这是提示，不代表错误。")
                if anomalies.empty: st.info("没有发现明显异常高额交易，或数据量不足。")
                else:
                    display = anomalies[["date", "item", "category", "amount"]].copy().head(20); display["date"] = display["date"].dt.strftime("%Y-%m-%d"); display.columns = ["日期", "项目", "类别", "金额"]; st.dataframe(display, hide_index=True, use_container_width=True, height=420, column_config={"金额": st.column_config.NumberColumn(format="RM %.2f")})
            with i_right:
                st.markdown('<div class="wy-section-title">高频／疑似固定支出</div>', unsafe_allow_html=True); st.caption("同名项目出现至少 3 次，方便快速发现订阅、通勤或固定消费。")
                if recurrences.empty: st.info("没有找到出现至少 3 次的同名项目。")
                else:
                    show = recurrences.head(20).copy(); show["最近日期"] = pd.to_datetime(show["最近日期"]).dt.strftime("%Y-%m-%d"); st.dataframe(show, hide_index=True, use_container_width=True, height=420, column_config={"总支出": st.column_config.NumberColumn(format="RM %.2f"), "平均每笔": st.column_config.NumberColumn(format="RM %.2f")})
            st.markdown('<div class="wy-section-title">数据质量检查</div>', unsafe_allow_html=True)
            blank_items = int((year_all["item"].str.strip() == "").sum()); zero_amounts = int((year_all["amount"] <= 0).sum()); duplicate_cols = ["date", "item", "category", "type", "amount", "note"]; duplicates = int(year_all.duplicated(subset=duplicate_cols, keep=False).sum())
            dq1, dq2, dq3 = st.columns(3); dq1.metric("空项目名称", blank_items); dq2.metric("零或负金额", zero_amounts); dq3.metric("疑似重复记录", duplicates)


elif navigation == "AI 洞察":
    page_header("AI 洞察", "AI 负责解释趋势；金额统计由本地数据计算完成。")
    expense_years = sorted(transactions.loc[transactions["type"] == EXPENSE, "date"].dt.year.unique().tolist(), reverse=True) if not transactions.empty else []
    if not expense_years:
        st.markdown('<div class="wy-empty">暂无支出数据可分析</div>', unsafe_allow_html=True)
    else:
        ai_year = st.selectbox("分析年份", expense_years); year_expenses = transactions[(transactions["date"].dt.year == int(ai_year)) & (transactions["type"] == EXPENSE)].copy(); classify_col, reset_col, _ = st.columns([1.2, 1, 3])
        if classify_col.button("AI 宏观归类", type="primary", use_container_width=True):
            with st.spinner("正在归类项目..."): mapping = categorize_macro(json.dumps(year_expenses["item"].unique().tolist(), ensure_ascii=False))
            if mapping:
                result = year_expenses.copy(); result["宏观类别"] = result["item"].map(mapping).fillna("其他"); st.session_state["macro_result"] = result; st.session_state["macro_year"] = int(ai_year); st.rerun()
            else: st.error("AI 归类失败，请稍后重试。")
        if reset_col.button("清除分析", use_container_width=True): st.session_state.pop("macro_result", None); st.session_state.pop("macro_year", None); st.rerun()
        if st.session_state.get("macro_year") == int(ai_year):
            macro = st.session_state["macro_result"].groupby("宏观类别")["amount"].sum().sort_values().reset_index(); chart = px.bar(macro, x="amount", y="宏观类别", orientation="h", labels={"amount": "支出 (RM)", "宏观类别": ""}, color_discrete_sequence=["#5B8FF9"]); chart.update_xaxes(rangemode="tozero", tickprefix="RM "); chart_layout(chart, 420, False, "closest"); st.plotly_chart(chart, use_container_width=True, config=CHART_CONFIG)
        st.divider(); st.markdown('<div class="wy-section-title">与账单对话</div>', unsafe_allow_html=True); st.caption("只发送年度汇总、类别统计与最高金额记录，不发送完整账本。")
        if "ai_chat_history" not in st.session_state: st.session_state["ai_chat_history"] = []
        for message in st.session_state["ai_chat_history"]:
            with st.chat_message(message["role"]): st.markdown(message["content"])
        question = st.chat_input("例如：哪一个月支出最高？主要原因是什么？")
        if question:
            st.session_state["ai_chat_history"].append({"role": "user", "content": question}); annual = monthly_summary(transactions, int(ai_year)); summary = {"year": int(ai_year), "total_expense": round(float(year_expenses["amount"].sum()), 2), "monthly_expense": dict(zip(annual["month"].astype(str), annual["支出"].round(2))), "category_expense": year_expenses.groupby("category")["amount"].sum().sort_values(ascending=False).round(2).to_dict(), "largest_transactions": year_expenses.nlargest(15, "amount")[["date", "item", "category", "amount"]].astype({"date": str}).to_dict("records")}; history = "\n".join(f"{message['role']}: {message['content']}" for message in st.session_state["ai_chat_history"][-6:]); prompt = f"你是私人财务分析助手。只根据资料回答，不编造交易。使用中文，金额使用 RM 两位小数。\n资料：{json.dumps(summary, ensure_ascii=False, default=str)}\n对话：{history}\n问题：{question}"
            try:
                with st.chat_message("assistant"):
                    with st.spinner("正在分析..."): reply = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt).text; st.markdown(reply)
                st.session_state["ai_chat_history"].append({"role": "assistant", "content": reply})
            except Exception as exc: st.error(f"AI 对话失败：{exc}")


else:
    page_header("设置与备份", "管理类别、导出备份，以及安全导入历史数据。")
    category_tab, backup_tab = st.tabs(["类别管理", "备份与导入"])
    with category_tab:
        usage = transactions.groupby("category").agg(使用笔数=("amount", "size"), 累计金额=("amount", "sum")).reset_index().rename(columns={"category": "类别"}) if not transactions.empty else pd.DataFrame(columns=["类别", "使用笔数", "累计金额"])
        st.dataframe(usage, hide_index=True, use_container_width=True, column_config={"累计金额": st.column_config.NumberColumn(format="RM %.2f")})
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown('<div class="wy-section-title">新增类别</div>', unsafe_allow_html=True); new_name = st.text_input("类别名称", key="settings_new_category")
            if st.button("新增类别", type="primary", use_container_width=True) and create_category(new_name): st.rerun()
        with right:
            st.markdown('<div class="wy-section-title">改名或合并类别</div>', unsafe_allow_html=True); source = st.selectbox("原类别", categories, key="merge_source"); target_mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True)
            if target_mode == "现有类别":
                choices = [category for category in categories if category != source]; target = st.selectbox("目标类别", choices, key="merge_target") if choices else ""
            else: target = st.text_input("新类别名称", key="merge_new_name")
            st.caption("会同时更新旧交易，再删除原类别。")
            if st.button("执行改名／合并", use_container_width=True) and rename_or_merge_category(source, target): st.rerun()
    with backup_tab:
        if not transactions.empty:
            export = transactions.copy(); export["date"] = export["date"].dt.date; excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer: export.to_excel(writer, index=False, sheet_name="Transactions")
            d1, d2 = st.columns(2); d1.download_button("下载 Excel", excel_buffer.getvalue(), f"WY_Wallet_V2_{date.today()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True); d2.download_button("下载 CSV", export.to_csv(index=False).encode("utf-8-sig"), f"WY_Wallet_V2_{date.today()}.csv", mime="text/csv", use_container_width=True)
        st.warning("导入只会新增，不会覆盖或删除。系统会提示疑似重复记录。")
        imported_file = st.file_uploader("上传 CSV 或 Excel", type=["csv", "xlsx"], key="import_file")
        if imported_file:
            try:
                imported = pd.read_csv(imported_file) if imported_file.name.lower().endswith(".csv") else pd.read_excel(imported_file); required = {"date", "item", "category", "type", "amount"}; missing = required - set(imported.columns)
                if missing: st.error("缺少栏位：" + ", ".join(sorted(missing)))
                else:
                    if "note" not in imported.columns: imported["note"] = ""
                    preview = imported[["date", "item", "category", "type", "amount", "note"]].copy(); normalized_preview = pd.DataFrame(normalize_transactions(preview)); existing_keys = set(tuple(row) for row in transactions.assign(date=transactions["date"].dt.date.astype(str))[["date", "item", "category", "type", "amount", "note"]].astype(str).to_numpy()); normalized_keys = normalized_preview[["date", "item", "category", "type", "amount", "note"]].astype(str).apply(tuple, axis=1); normalized_preview["疑似重复"] = normalized_keys.isin(existing_keys)
                    st.dataframe(normalized_preview.head(100), hide_index=True, use_container_width=True, column_config={"amount": st.column_config.NumberColumn("金额", format="RM %.2f"), "疑似重复": st.column_config.CheckboxColumn()}); duplicate_count = int(normalized_preview["疑似重复"].sum()); st.caption(f"共 {len(normalized_preview)} 笔，其中 {duplicate_count} 笔疑似已存在。"); skip_duplicates = st.checkbox("跳过疑似重复记录", value=True); confirm = st.checkbox("我已检查预览并确认导入"); rows_to_import = normalized_preview[~normalized_preview["疑似重复"]] if skip_duplicates else normalized_preview
                    if st.button("开始导入", type="primary", disabled=not confirm, use_container_width=True):
                        rows_to_import = rows_to_import.drop(columns=["疑似重复"])
                        if rows_to_import.empty: st.warning("没有可导入的新记录。")
                        elif insert_transactions(rows_to_import): st.rerun()
            except Exception as exc: st.error(f"读取或导入失败：{exc}")
