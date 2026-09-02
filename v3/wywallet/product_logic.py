from __future__ import annotations

from datetime import date

import pandas as pd

from .config import EXPENSE, now_my, today_my


def tracking_start_date(transactions: pd.DataFrame | None) -> date | None:
    if not isinstance(transactions, pd.DataFrame) or transactions.empty or "date" not in transactions:
        return None
    dates = pd.to_datetime(transactions["date"], errors="coerce")
    valid = dates[dates.notna() & (dates.dt.date <= today_my())]
    return None if valid.empty else valid.min().date()


def tracked_month_count(transactions: pd.DataFrame | None, start: date, end: date) -> int:
    first = tracking_start_date(transactions)
    effective_start = max(start, first) if first is not None else start
    if effective_start > end:
        return 0
    return (end.year - effective_start.year) * 12 + end.month - effective_start.month + 1


def historical_monthly_average(annual: pd.DataFrame, year: int, transactions: pd.DataFrame) -> float | None:
    now = now_my()
    year = int(year)
    first = tracking_start_date(transactions)
    if year > now.year or first is None or first.year > year:
        return None

    if year < now.year:
        start_month = first.month if first.year == year else 1
        end_month = 12
    else:
        completed = now.month - 1
        if completed <= 0:
            start_month = now.month
            end_month = now.month
        else:
            start_month = first.month if first.year == year else 1
            end_month = completed
            if start_month > end_month:
                start_month = now.month
                end_month = now.month

    months = max(end_month - start_month + 1, 1)
    total = float(annual.loc[annual["month"].between(start_month, end_month), "支出"].sum())
    return total / months


def invalid_quality_for_year(invalid_rows: pd.DataFrame, year: int) -> tuple[int, int]:
    if invalid_rows.empty or "date" not in invalid_rows:
        return 0, 0
    dates = pd.to_datetime(invalid_rows["date"], errors="coerce")
    assigned = int((dates.notna() & (dates.dt.year == int(year))).sum())
    unassigned = int(dates.isna().sum())
    return assigned, unassigned


def recurring_items_by_category(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["项目", "类别", "次数", "覆盖月份", "总支出", "平均每笔", "金额波动", "典型间隔(天)", "规律程度", "最近日期"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    work = frame[pd.to_datetime(frame["date"], errors="coerce").dt.date <= today_my()].copy()
    work = work[work["type"] == EXPENSE].copy()
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["_item_key"] = work["item"].fillna("").astype(str).str.strip().str.casefold()
    work["_category_key"] = work["category"].fillna("").astype(str).str.strip().str.casefold()
    rows: list[dict] = []
    for _, group in work.groupby(["_item_key", "_category_key"], dropna=False):
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
            "项目": str(group.iloc[0]["item"]),
            "类别": str(group.iloc[0]["category"]),
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
    rank = {"高": 0, "中": 1}
    result = pd.DataFrame(rows, columns=columns)
    result["_rank"] = result["规律程度"].map(rank).fillna(9)
    return result.sort_values(["_rank", "总支出"], ascending=[True, False]).drop(columns="_rank").reset_index(drop=True)
