from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

from .config import EXPENSE, now_my, today_my


def tracking_start_date(transactions: pd.DataFrame | None) -> date | None:
    if not isinstance(transactions, pd.DataFrame) or transactions.empty or "date" not in transactions:
        return None
    dates = pd.to_datetime(transactions["date"], errors="coerce")
    valid = dates[dates.notna() & (dates.dt.date <= today_my())]
    return None if valid.empty else valid.min().date()


def first_complete_tracking_month(transactions: pd.DataFrame | None, year: int) -> int | None:
    """Return the first complete calendar month available in ``year``.

    If ledger tracking began after day 1, that first calendar month is partial and
    is excluded once later complete months exist. This avoids treating a one-day
    month at the beginning of the ledger as a full monthly observation.
    """
    first = tracking_start_date(transactions)
    year = int(year)
    if first is None or first.year > year:
        return None
    if first.year < year:
        return 1
    month = first.month + (1 if first.day > 1 else 0)
    return month if month <= 12 else None


def tracked_month_count(transactions: pd.DataFrame | None, start: date, end: date) -> int:
    first = tracking_start_date(transactions)
    effective_start = max(start, first) if first is not None else start
    if effective_start > end:
        return 0
    if effective_start.day > 1:
        next_month = pd.Timestamp(effective_start).replace(day=1) + pd.DateOffset(months=1)
        effective_start = next_month.date()
    effective_end = end
    if effective_end.day < calendar.monthrange(effective_end.year, effective_end.month)[1]:
        effective_end = (pd.Timestamp(effective_end).replace(day=1) - pd.DateOffset(days=1)).date()
    if effective_start > effective_end:
        return 0
    return (effective_end.year - effective_start.year) * 12 + effective_end.month - effective_start.month + 1


def historical_monthly_average(annual: pd.DataFrame, year: int, transactions: pd.DataFrame) -> float | None:
    now = now_my()
    year = int(year)
    first = tracking_start_date(transactions)
    if year > now.year or first is None or first.year > year:
        return None

    start_month = first_complete_tracking_month(transactions, year)
    if year < now.year:
        if start_month is None:
            return None
        end_month = 12
    else:
        completed = now.month - 1
        if start_month is not None and start_month <= completed:
            end_month = completed
        else:
            # No complete tracked month exists yet. Preserve the useful existing
            # behavior by showing the current partial month actual rather than an
            # invented monthly average or zero.
            if first.year == year and first <= today_my():
                return float(annual.loc[annual["month"] == now.month, "支出"].sum())
            return None

    months = end_month - start_month + 1
    if months <= 0:
        return None
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
