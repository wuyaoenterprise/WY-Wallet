from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

APP_TITLE = "WY Wallet V3"
APP_VERSION = "2026.09.01-v3.1.0"
BUILD_ID = "v3-final-hardening-r1"
TIMEZONE_NAME = "Asia/Kuala_Lumpur"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
CURRENCY = "MYR"
CURRENCY_SYMBOL = "RM"
GEMINI_MODEL = "gemini-3.7-flash"
EXPENSE = "Expense"
INCOME = "Income"
REFUND = "Refund"
TRANSACTION_TYPES = [EXPENSE, INCOME, REFUND]
TYPE_LABELS = {EXPENSE: "支出", INCOME: "收入", REFUND: "退款"}
DEFAULT_CATEGORIES = ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "教育", "投资", "旅游", "其他"]
ADD_CATEGORY_OPTION = "＋ 新增类别"
MONTH_LABELS = [f"{month}月" for month in range(1, 13)]
DB_BATCH_SIZE = 1000
MAX_TRANSACTION_ROWS = 100_000
AI_RETRY_ATTEMPTS = 3
AI_MACRO_BATCH_SIZE = 400
RECEIPT_TOTAL_TOLERANCE = 0.05
REFUND_DB_MARKER = "[WY_REFUND_V3]"
# Fragments now handle most click interactions, so a moderate shared cache is
# enough to avoid repeated Supabase pagination without keeping data stale for
# several minutes. Every V3 write and explicit refresh invalidates it globally.
UI_CACHE_TTL_SECONDS = 120


def now_my() -> datetime:
    return datetime.now(TIMEZONE)


def today_my() -> date:
    return now_my().date()
