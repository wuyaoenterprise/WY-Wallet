from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from wywallet import reports_page


ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v3_keeps_core_user_facing_features_from_earlier_wallet_versions():
    app = _source("v3/app.py")
    reports = _source("v3/wywallet/reports_page.py")
    ai = _source("v3/wywallet/ai_page.py")
    settings = _source("v3/wywallet/settings_page.py")

    # These are intentional user-facing capabilities, not disposable UI details.
    assert 'st.page_link("pages/receipt.py"' in app
    assert '"总览", "交易记录", "分析报表", "AI 洞察", "设置与备份"' in app
    assert "开销日历" in reports
    assert "_render_monthly_expense_calendar" in reports
    assert "calendar.Calendar(firstweekday=0).monthdayscalendar" in reports
    assert "AI 宏观归类" in ai
    assert "与账单对话" in ai
    assert '"类别管理", "数据修复", "备份"' in settings
    assert "完整备份" in settings


def test_monthly_calendar_shows_gross_spending_and_refunds_separately(monkeypatch):
    rendered: list[str] = []
    captions: list[str] = []
    monkeypatch.setattr(reports_page.st, "markdown", lambda body, **kwargs: rendered.append(str(body)))
    monkeypatch.setattr(reports_page.st, "caption", lambda body, **kwargs: captions.append(str(body)))
    monkeypatch.setattr(reports_page, "now_my", lambda: datetime(2026, 9, 8, 12, 0, 0))

    effects = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-09-08"), "type": "Expense", "amount": 12.50},
            {"date": pd.Timestamp("2026-09-08"), "type": "Expense", "amount": 7.50},
            {"date": pd.Timestamp("2026-09-08"), "type": "Refund", "amount": 5.00},
            {"date": pd.Timestamp("2026-09-09"), "type": "Expense", "amount": 3.25},
        ]
    )

    reports_page._render_monthly_expense_calendar(effects, 2026, 9)

    html = "\n".join(rendered)
    assert "RM 20.00" in html
    assert "退款 RM 5.00" in html
    assert "RM 3.25" in html
    assert "今天" in html
    assert any("主金额＝当天毛消费" in text and "净支出" in text for text in captions)
