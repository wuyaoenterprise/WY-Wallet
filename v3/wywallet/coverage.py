from __future__ import annotations

import calendar
from datetime import date

import numpy as np
import pandas as pd

from . import analytics
from .config import now_my, today_my
from .product_logic import tracking_start_date


def safe_change_ratio(current: float, baseline: float) -> float | None:
    """Return a meaningful percentage change only for a positive baseline.

    A zero or refund-heavy (negative) net-expense baseline cannot support a
    conventional spending-growth percentage without producing a misleading sign.
    """
    base = float(baseline)
    return None if base <= 0 else (float(current) - base) / base


def tracked_annual_view(
    annual: pd.DataFrame,
    year: int,
    transactions: pd.DataFrame,
    *,
    through_month: int | None = None,
) -> pd.DataFrame:
    """Return only calendar months that were actually under ledger tracking.

    Months before the first-ever transaction are unknown coverage, not zero-spend
    observations. The first tracked month is retained and explicitly marked as
    partial when tracking began after day 1.
    """
    work = annual.copy()
    first = tracking_start_date(transactions)
    year = int(year)
    if first is not None and first.year == year:
        work = work[work["month"] >= first.month].copy()
    if through_month is not None:
        work = work[work["month"] <= int(through_month)].copy()
    work["partial_tracking"] = False
    if first is not None and first.year == year and first.day > 1:
        work.loc[work["month"] == first.month, "partial_tracking"] = True
    return work.reset_index(drop=True)


def is_partial_tracking_year(transactions: pd.DataFrame, year: int) -> bool:
    first = tracking_start_date(transactions)
    return bool(first is not None and first.year == int(year) and (first.month > 1 or first.day > 1))


def prior_year_has_partial_coverage(transactions: pd.DataFrame, year: int) -> bool:
    """Whether year-1 lacks the beginning of the comparable calendar year."""
    first = tracking_start_date(transactions)
    prior = int(year) - 1
    return bool(first is not None and first.year == prior and (first.month > 1 or first.day > 1))


def _safe_anniversary(value: date, year: int) -> date:
    try:
        return value.replace(year=int(year))
    except ValueError:
        return value.replace(year=int(year), day=28)


def same_period_yoy(transactions: pd.DataFrame, year: int) -> dict | None:
    """Compute YoY only on dates for which both years have ledger coverage.

    If the prior comparison year is the first partial tracking year, the current
    period is aligned to the same anniversary so untracked prior months are never
    interpreted as RM0. If there is no prior coverage at all, comparison is
    explicitly unavailable.
    """
    today = today_my()
    year = int(year)
    if year > today.year:
        return None

    current_start = date(year, 1, 1)
    current_end = date(year, 12, 31) if year < today.year else today
    previous_start = date(year - 1, 1, 1)
    previous_end = date(year - 1, 12, 31) if year < today.year else _safe_anniversary(current_end, year - 1)
    first = tracking_start_date(transactions)
    coverage_aligned = False
    reason: str | None = None

    if first is not None:
        if first > previous_end:
            reason = f"上一对比区间在账本开始追踪前；账本从 {first:%Y-%m-%d} 才开始。"
        elif first > previous_start:
            previous_start = first
            current_start = max(current_start, _safe_anniversary(first, year))
            coverage_aligned = True
            if current_start > current_end:
                reason = f"今年尚未走到可与 {first:%Y-%m-%d} 起始覆盖期比较的日期。"

    effects = analytics.expense_effect_frame(transactions)
    if effects.empty:
        current_total = previous_total = 0.0
    else:
        dates = effects["date"].dt.date
        current_total = float(
            effects.loc[(dates >= current_start) & (dates <= current_end), "expense_effect"].sum()
        ) if current_start <= current_end else 0.0
        previous_total = float(
            effects.loc[(dates >= previous_start) & (dates <= previous_end), "expense_effect"].sum()
        ) if previous_start <= previous_end else 0.0

    change = None if reason or previous_total <= 0 else (current_total - previous_total) / previous_total
    return {
        "current_total": current_total,
        "previous_total": previous_total,
        "change": change,
        "current_start": current_start,
        "current_end": current_end,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "coverage_aligned": coverage_aligned,
        "reason": reason,
    }


def historical_month_end_forecast(
    frame: pd.DataFrame,
    year: int,
    month: int,
    elapsed_day: int,
    lookback: int = 6,
) -> dict[str, float | int | str]:
    """Forecast using only fully tracked history, including genuine zero months.

    A first partial tracking month is excluded because it is not a complete
    observation. Once tracking has begun for full months, a month with no recorded
    spending contributes a real zero remaining-spend sample instead of being
    silently dropped (which would bias the forecast upward).
    """
    current = analytics.month_slice(frame, year, month)
    _, current_expense, _ = analytics.calculate_totals(current)
    days_in_current = calendar.monthrange(int(year), int(month))[1]
    elapsed = min(max(int(elapsed_day), 1), days_in_current)
    if elapsed >= days_in_current:
        value = round(current_expense, 2)
        return {"forecast": value, "low": value, "high": value, "history_months": 0, "method": "actual"}

    first = tracking_start_date(frame)
    samples: list[float] = []
    anchor = pd.Timestamp(year=int(year), month=int(month), day=1)
    for offset in range(1, max(0, int(lookback)) + 1):
        period = (anchor - pd.DateOffset(months=offset)).to_period("M")
        py, pm = int(period.year), int(period.month)
        period_start = date(py, pm, 1)
        period_end = date(py, pm, calendar.monthrange(py, pm)[1])
        if first is not None:
            if period_end < first:
                continue
            if first.year == py and first.month == pm and first.day > 1:
                continue
        whole = analytics.month_slice(frame, py, pm)
        cutoff = min(elapsed, calendar.monthrange(py, pm)[1])
        remaining = whole[whole["date"].dt.day > cutoff].copy() if not whole.empty else whole.copy()
        _, remaining_expense, _ = analytics.calculate_totals(remaining)
        samples.append(float(remaining_expense))

    if not samples:
        value = round(current_expense, 2)
        return {"forecast": value, "low": value, "high": value, "history_months": 0, "method": "actual_to_date"}

    median_remaining = float(np.median(samples))
    q25 = float(np.percentile(samples, 25))
    q75 = float(np.percentile(samples, 75))
    point = round(current_expense + median_remaining, 2)
    low = round(current_expense + min(q25, q75), 2)
    high = round(current_expense + max(q25, q75), 2)
    return {
        "forecast": point,
        "low": low,
        "high": high,
        "history_months": len(samples),
        "method": "historical_remaining_median",
    }
