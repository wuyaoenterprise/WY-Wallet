from __future__ import annotations

import io

import pandas as pd

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_spreadsheet_text(value):
    if not isinstance(value, str) or not value:
        return value
    candidate = value.lstrip("\ufeff")
    if candidate.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def sanitize_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in work.columns:
        if work[column].dtype == object:
            work[column] = work[column].map(sanitize_spreadsheet_text)
    return work


def build_backup_excel(
    transactions: pd.DataFrame,
    categories: pd.DataFrame,
    metadata: pd.DataFrame,
    invalid_rows: pd.DataFrame,
) -> bytes:
    excel = io.BytesIO()
    with pd.ExcelWriter(
        excel,
        engine="xlsxwriter",
        engine_kwargs={"options": {"strings_to_formulas": False, "strings_to_urls": False}},
    ) as writer:
        sanitize_export_frame(transactions).to_excel(writer, index=False, sheet_name="Transactions")
        sanitize_export_frame(categories).to_excel(writer, index=False, sheet_name="Categories")
        sanitize_export_frame(metadata).to_excel(writer, index=False, sheet_name="Metadata")
        sanitize_export_frame(invalid_rows).to_excel(writer, index=False, sheet_name="InvalidRows")
    return excel.getvalue()


def safe_csv_bytes(frame: pd.DataFrame) -> bytes:
    return sanitize_export_frame(frame).to_csv(index=False).encode("utf-8-sig")
