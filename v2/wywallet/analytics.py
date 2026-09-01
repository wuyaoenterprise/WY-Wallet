from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import EXPENSE, INCOME, MONTH_LABELS, REFUND, now_my, today_my


def posted_only(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "date" not in frame:
        return frame.copy()
    dates = pd.to_datetime(frame["date"], errors="coerce")
    return frame[dates.dt.date <= today_my()].copy()


def month_slice(frame: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    frame = posted_only(frame)
    if frame.empty:
        return frame.copy()
    return frame[(frame["date"].dt.year == int(year)) & (frame["date"].dt.month == int(month))].copy()


def expense_effect_frame(frame: pd.DataFrame) -> pd.DataFrame:
    frame = posted_only(frame)
    if frame.empty:
        work = frame.copy()
        work["expense_effect"] = pd.Series(dtype=float)
        return work
    work = frame[frame["type"].isin([EXPENSE, REFUND])].copy()
    work["expense_effect"] = np.where(work["type"] == REFUND, -work["amount"].astype(float), work["amount"].astype(float))
    return work


def calculate_totals(frame: pd.DataFrame) -> tuple[float, float, float]:
    frame = posted_only(frame)
    if frame.empty:
        return 0.0, 0.0, 0.0
    income = float(frame.loc[frame["type"] == INCOME, "amount"].sum())
    gross_expense = float(frame.loc[frame["type"] == EXPENSE, "amount"].sum())
    refunds = float(frame.loc[frame["type"] == REFUND, "amount"].sum())
    net_expense = gross_expense - refunds
    return income, net_expense, income - net_expense


def calculate_flow_totals(frame: pd.DataFrame) -> dict[str, float]:
    frame = posted_only(frame)
    return {
        "income": float(frame.loc[frame["type"] == INCOME, "amount"].sum()) if not frame.empty else 0.0,
        "gross_expense": float(frame.loc[frame["type"] == EXPENSE, "amount"].sum()) if not frame.empty else 0.0,
        "refund": float(frame.loc[frame["type"] == REFUND, "amount"].sum()) if not frame.empty else 0.0,
    }


def monthly_summary(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    base = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS})
    work = posted_only(frame)
    year_data = work[work["date"].dt.year == int(year)].copy() if not work.empty else work.copy()
    if year_data.empty:
        base["收入"] = 0.0
        base["毛支出"] = 0.0
        base["退款"] = 0.0
    else:
        grouped = year_data.assign(month=year_data["date"].dt.month).pivot_table(
            index="month", columns="type", values="amount", aggfunc="sum", fill_value=0
        ).reset_index()
        for column in [INCOME, EXPENSE, REFUND]:
            if column not in grouped.columns:
                grouped[column] = 0.0
        grouped = grouped.rename(columns={INCOME: "收入", EXPENSE: "毛支出", REFUND: "退款"})
        base = base.merge(grouped[["month", "收入", "毛支出", "退款"]], on="month", how="left").fillna(0.0)
    base["支出"] = base["毛支出"] - base["退款"]
    base["结余"] = base["收入"] - base["支出"]
    base["储蓄率"] = np.where(base["收入"] > 0, base["结余"] / base["收入"] * 100, np.nan)
    base["累计支出"] = base["支出"].cumsum()
    return base


def elapsed_month_count(year: int) -> int:
    now = now_my()
    if int(year) < now.year:
        return 12
    if int(year) == now.year:
        return now.month
    return 0


def average_monthly_expense(annual: pd.DataFrame, year: int) -> float | None:
    months = elapsed_month_count(year)
    if months <= 0:
        return None
    return float(annual.loc[annual["month"] <= months, "支出"].sum()) / months


def annual_savings_rate(annual: pd.DataFrame) -> float | None:
    income = float(annual["收入"].sum())
    expense = float(annual["支出"].sum())
    return None if income <= 0 else (income - expense) / income * 100


def recent_months_summary(frame: pd.DataFrame, periods: int = 12) -> pd.DataFrame:
    now_period = pd.Period(now_my().replace(tzinfo=None), freq="M")
    period_index = pd.period_range(end=now_period, periods=periods, freq="M")
    base = pd.DataFrame({"period": period_index, "月份": [p.strftime("%Y-%m") for p in period_index]})
    effects = expense_effect_frame(frame)
    if effects.empty:
        base["支出"] = 0.0
        return base
    effects["period"] = effects["date"].dt.to_period("M")
    grouped = effects.groupby("period")["expense_effect"].sum().reset_index(name="支出")
    base = base.merge(grouped, on="period", how="left")
    base["支出"] = base["支出"].fillna(0.0)
    return base


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _safe_anniversary(value: date, year: int) -> date:
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def same_period_yoy(frame: pd.DataFrame, year: int) -> dict | None:
    today = today_my()
    if year > today.year:
        return None
    current_start = date(year, 1, 1)
    current_end = date(year, 12, 31) if year < today.year else today
    previous_start = date(year - 1, 1, 1)
    previous_end = date(year - 1, 12, 31) if year < today.year else _safe_anniversary(current_end, year - 1)
    work = expense_effect_frame(frame)
    if work.empty:
        current_total = previous_total = 0.0
    else:
        dates = work["date"].dt.date
        current_total = float(work.loc[(dates >= current_start) & (dates <= current_end), "expense_effect"].sum())
        previous_total = float(work.loc[(dates >= previous_start) & (dates <= previous_end), "expense_effect"].sum())
    change = None if previous_total == 0 else (current_total - previous_total) / abs(previous_total)
    return {
        "current_total": current_total,
        "previous_total": previous_total,
        "change": change,
        "current_start": current_start,
        "current_end": current_end,
        "previous_start": previous_start,
        "previous_end": previous_end,
    }


def net_expense_by_category(frame: pd.DataFrame) -> pd.DataFrame:
    effects = expense_effect_frame(frame)
    if effects.empty:
        return pd.DataFrame(columns=["category", "amount"])
    result = effects.groupby("category", dropna=False)["expense_effect"].sum().reset_index(name="amount")
    return result.sort_values("amount", ascending=False)


def weekday_average(frame: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    effects = expense_effect_frame(month_slice(frame, year, month))
    totals = effects.assign(weekday=effects["date"].dt.dayofweek).groupby("weekday")["expense_effect"].sum() if not effects.empty else pd.Series(dtype=float)
    today = today_my()
    month_start = date(int(year), int(month), 1)
    month_end = date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])
    effective_end = min(month_end, today) if month_start <= today else month_start - timedelta(days=1)
    counts = {weekday: 0 for weekday in range(7)}
    cursor = month_start
    while cursor <= effective_end:
        counts[cursor.weekday()] += 1
        cursor += timedelta(days=1)
    return pd.DataFrame([
        {"weekday": w, "星期": names[w], "总支出": float(totals.get(w, 0.0)), "出现次数": counts[w],
         "平均每个该星期": float(totals.get(w, 0.0)) / counts[w] if counts[w] else 0.0}
        for w in range(7)
    ])


def anomaly_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    expenses = posted_only(frame)
    expenses = expenses[expenses["type"] == EXPENSE].copy() if not expenses.empty else expenses
    if len(expenses) < 5:
        return pd.DataFrame(columns=expenses.columns)
    candidates: list[pd.DataFrame] = []
    for _, group in expenses.groupby("category", dropna=False):
        if len(group) < 5:
            continue
        q1, q3 = group["amount"].quantile([0.25, 0.75])
        iqr = float(q3 - q1)
        threshold = float(q3 + 1.5 * iqr) if iqr > 0 else float(group["amount"].mean() + 2 * group["amount"].std(ddof=0))
        candidates.append(group[group["amount"] > max(threshold, 0)])
    if not candidates:
        return pd.DataFrame(columns=expenses.columns)
    subset = ["id"] if "id" in expenses.columns else None
    return pd.concat(candidates).drop_duplicates(subset=subset).sort_values("amount", ascending=False)


@dataclass
class RecurringRule:
    item: str
    count: int
    months: int
    total: float
    average: float
    amount_cv: float
    median_gap_days: float | None
    regularity: str
    last_date: pd.Timestamp


def recurring_items(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["项目", "次数", "覆盖月份", "总支出", "平均每笔", "金额波动", "典型间隔(天)", "规律程度", "最近日期"]
    work = posted_only(frame)
    expenses = work[work["type"] == EXPENSE].copy() if not work.empty else work
    if expenses.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict] = []
    expenses["_key"] = expenses["item"].fillna("").astype(str).str.strip().str.casefold()
    for _, group in expenses.groupby("_key"):
        if len(group) < 3:
            continue
        group = group.sort_values("date")
        months = int(group["date"].dt.to_period("M").nunique())
        if months < 2:
            continue
        mean = float(group["amount"].mean())
        cv = float(group["amount"].std(ddof=0) / mean) if mean > 0 else 999.0
        gaps = group["date"].drop_duplicates().sort_values().diff().dt.days.dropna()
        median_gap = float(gaps.median()) if not gaps.empty else None
        monthly_cadence = median_gap is not None and 20 <= median_gap <= 40
        stable_amount = cv <= 0.25
        one_or_few = len(group) <= months * 2.2
        if monthly_cadence and stable_amount:
            regularity = "高"
        elif (monthly_cadence or stable_amount) and one_or_few:
            regularity = "中"
        else:
            continue
        rows.append({
            "项目": str(group.iloc[0]["item"]), "次数": int(len(group)), "覆盖月份": months,
            "总支出": round(float(group["amount"].sum()), 2), "平均每笔": round(mean, 2),
            "金额波动": cv, "典型间隔(天)": None if median_gap is None else round(median_gap, 1),
            "规律程度": regularity, "最近日期": group["date"].max(),
        })
    if not rows:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame(rows)
    result["_order"] = result["规律程度"].map({"高": 0, "中": 1})
    return result.sort_values(["_order", "总支出"], ascending=[True, False]).drop(columns="_order")


def literal_search(frame: pd.DataFrame, text: str) -> pd.DataFrame:
    if frame.empty or not str(text or ""):
        return frame.copy()
    query = str(text)
    mask = (
        frame["item"].fillna("").astype(str).str.contains(query, case=False, na=False, regex=False)
        | frame["category"].fillna("").astype(str).str.contains(query, case=False, na=False, regex=False)
        | frame["note"].fillna("").astype(str).str.contains(query, case=False, na=False, regex=False)
    )
    return frame[mask].copy()


def data_quality(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {"blank_items": 0, "nonpositive_amounts": 0, "duplicates": 0}
    blank = int(frame["item"].fillna("").astype(str).str.strip().eq("").sum())
    nonpositive = int((pd.to_numeric(frame["amount"], errors="coerce").fillna(0) <= 0).sum())
    cols = [c for c in ["date", "item", "category", "type", "amount", "note"] if c in frame.columns]
    duplicates = int(frame.duplicated(subset=cols, keep=False).sum()) if cols else 0
    return {"blank_items": blank, "nonpositive_amounts": nonpositive, "duplicates": duplicates}
