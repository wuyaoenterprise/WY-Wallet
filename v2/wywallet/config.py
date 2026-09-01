from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

APP_TITLE = "WY Wallet V2"
TIMEZONE_NAME = "Asia/Kuala_Lumpur"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
CURRENCY = "MYR"
CURRENCY_SYMBOL = "RM"
GEMINI_MODEL = "gemini-3.7-flash"
EXPENSE = "Expense"
INCOME = "Income"
TYPE_LABELS = {EXPENSE: "支出", INCOME: "收入"}
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
ADD_CATEGORY_OPTION = "＋ 新增类别"
MONTH_LABELS = [f"{month}月" for month in range(1, 13)]
DB_BATCH_SIZE = 1000
MAX_TRANSACTION_ROWS = 100_000
AI_RETRY_ATTEMPTS = 3
AI_MACRO_BATCH_SIZE = 400
RECEIPT_TOTAL_TOLERANCE = 0.05


def now_my() -> datetime:
    return datetime.now(TIMEZONE)


def today_my() -> date:
    return now_my().date()
