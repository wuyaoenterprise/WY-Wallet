from __future__ import annotations

import calendar
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import EXPENSE, INCOME, MONTH_LABELS, now_my


def month_slice(frame: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame[(frame["date"].dt.year == int(year)) & (frame["date"].dt.month == int(month))].copy()


def calculate_totals(frame: pd.DataFrame) -> tuple[float, float, float]:
    if frame.empty:
        return 0.0, 0.0, 0.0
    income = float(frame.loc[frame["type"] == INCOME, "amount"].sum())
    expense = float(frame.loc[frame["type"] == EXPENSE, "amount"].sum())
    return income, expense, income - expense


def monthly_summary(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    base = pd.DataFrame({"month": range(1, 13), "月份": MONTH_LABELS})
    year_data = frame[frame["date"].dt.year == int(year)].copy() if not frame.empty else frame.copy()
    if year_data.empty:
        base["收入"] = 0.0
        base["支出"] = 0.0
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
        base = base.merge(grouped[["month", "收入", "支出"]], on="month", how="left").fillna(0.0)
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
    if income <= 0:
        return None
    return (income - expense) / income * 100


def recent_months_summary(frame: pd.DataFrame, periods: int = 12) -> pd.DataFrame:
    now_period = pd.Period(now_my().replace(tzinfo=None), freq="M")
    period_index = pd.period_range(end=now_period, periods=periods, freq="M")
    base = pd.DataFrame({"period": period_index, "月份": [period.strftime("%Y-%m") for period in period_index]})
    expenses = frame[frame["type"] == EXPENSE].copy() if not frame.empty else frame.copy()
    if expenses.empty:
        base["支出"] = 0.0
        return base
    expenses["period"] = expenses["date"].dt.to_period("M")
    grouped = expenses.groupby("period")["amount"].sum().reset_index(name="支出")
    base = base.merge(grouped, on="period", how="left")
    base["支出"] = base["支出"].fillna(0.0)
    return base


def previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def weekday_average(expenses: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    totals = expenses.assign(weekday=expenses["date"].dt.dayofweek).groupby("weekday")["amount"].sum() if not expenses.empty else pd.Series(dtype=float)
    cal = calendar.Calendar(firstweekday=0)
    counts = {weekday: 0 for weekday in range(7)}
    for day in cal.itermonthdates(int(year), int(month)):
        if day.month == int(month):
            counts[day.weekday()] += 1
    rows = []
    for weekday in range(7):
        total = float(totals.get(weekday, 0.0))
        occurrences = counts[weekday]
        rows.append({"weekday": weekday, "星期": names[weekday], "总支出": total, "出现次数": occurrences, "平均每个该星期": total / occurrences if occurrences else 0.0})
    return pd.DataFrame(rows)


def anomaly_transactions(expenses: pd.DataFrame) -> pd.DataFrame:
    if len(expenses) < 5:
        return pd.DataFrame(columns=expenses.columns)
    candidates: list[pd.DataFrame] = []
    for _, group in expenses.groupby("category", dropna=False):
        if len(group) < 5:
            continue
        q1, q3 = group["amount"].quantile([0.25, 0.75])
        iqr = float(q3 - q1)
        if iqr > 0:
            threshold = float(q3 + 1.5 * iqr)
        else:
            threshold = float(group["amount"].mean() + 2 * group["amount"].std(ddof=0))
        candidates.append(group[group["amount"] > max(threshold, 0)])
    if not candidates:
        return pd.DataFrame(columns=expenses.columns)
    result = pd.concat(candidates).drop_duplicates(subset=["id"] if "id" in expenses.columns else None)
    return result.sort_values("amount", ascending=False)


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


def recurring_items(expenses: pd.DataFrame) -> pd.DataFrame:
    columns = ["项目", "次数", "覆盖月份", "总支出", "平均每笔", "金额波动", "典型间隔(天)", "规律程度", "最近日期"]
    if expenses.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    work = expenses.copy()
    work["_key"] = work["item"].fillna("").astype(str).str.strip().str.casefold()
    for _, group in work.groupby("_key"):
        if len(group) < 3:
            continue
        group = group.sort_values("date")
        months = int(group["date"].dt.to_period("M").nunique())
        if months < 2:
            continue
        mean = float(group["amount"].mean())
        cv = float(group["amount"].std(ddof=0) / mean) if mean > 0 else 999.0
        dates = group["date"].drop_duplicates().sort_values()
        gaps = dates.diff().dt.days.dropna()
        median_gap = float(gaps.median()) if not gaps.empty else None
        monthly_cadence = median_gap is not None and 20 <= median_gap <= 40
        stable_amount = cv <= 0.25
        one_or_few_per_month = len(group) <= months * 2.2
        if monthly_cadence and stable_amount:
            regularity = "高"
        elif (monthly_cadence or stable_amount) and one_or_few_per_month:
            regularity = "中"
        else:
            continue
        rows.append({
            "项目": str(group.iloc[0]["item"]),
            "次数": int(len(group)),
            "覆盖月份": months,
            "总支出": round(float(group["amount"].sum()), 2),
            "平均每笔": round(mean, 2),
            "金额波动": cv,
            "典型间隔(天)": None if median_gap is None else round(median_gap, 1),
            "规律程度": regularity,
            "最近日期": group["date"].max(),
        })
    if not rows:
        return pd.DataFrame(columns=columns)
    order = {"高": 0, "中": 1}
    result = pd.DataFrame(rows)
    result["_order"] = result["规律程度"].map(order)
    return result.sort_values(["_order", "总支出"], ascending=[True, False]).drop(columns="_order")


def literal_search(frame: pd.DataFrame, text: str) -> pd.DataFrame:
    """Case-insensitive literal search; user input is never interpreted as regex."""
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
    duplicate_cols = [column for column in ["date", "item", "category", "type", "amount", "note"] if column in frame.columns]
    duplicates = int(frame.duplicated(subset=duplicate_cols, keep=False).sum()) if duplicate_cols else 0
    return {"blank_items": blank, "nonpositive_amounts": nonpositive, "duplicates": duplicates}
