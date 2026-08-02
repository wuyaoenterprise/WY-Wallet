# WY Wallet V2

This directory contains the isolated V2 application. The existing production entry point in the repository root remains unchanged.

## Deploy as a separate Streamlit app

- Repository: `wuyaoenterprise/WY-Wallet`
- Branch: `agent/wy-wallet-v2-redesign`
- Main file: `v2/app.py`
- Copy the existing `SUPABASE_URL`, `SUPABASE_KEY`, and `GOOGLE_API_KEY` values into the new Streamlit app secrets.

V2 continues to use the existing Supabase `transactions` and `categories` tables. Changes made in either site therefore affect the same transaction data.

## Application structure

- `v2/app.py` is the stable Streamlit entry point.
- `v2/app_rich.py` contains the full interface, database operations, AI functions, and reports.
- `v2/pages/5_数据导入.py` is the rule-based external-data import wizard.
- `v2/pages/6_AI智能整理导入.py` uses Gemini 3.6 Flash to organize messy documents before review and import.
- The entry point uses `runpy` so the implementation executes again on every Streamlit rerun instead of being held in Python's normal import cache.
- `.github/workflows/v2-syntax-check.yml` checks that all V2 Python files compile.

## Current V2 design

- Sidebar navigation and compact dashboard
- Current-month income, expenses, balance, daily average, month comparison, and projected month-end spending
- Professional transaction table with search, filters, sorting, row selection, editing, safe deletion, and one-step undo
- Category creation integrated directly into add, edit, and receipt workflows
- Receipt recognition with editable results
- AI analysis receives summarized statistics rather than the complete yearly ledger
- Excel and CSV export
- Category rename/merge workflow that also updates old transactions

## Rule-based external import wizard

The standard import interface accepts CSV, XLSX, XLS, and JSON exports from other finance apps or banks.

- Automatically detects common Date, Description, Payee, Category, Amount, Debit, Credit, Type, Memo, and Currency columns
- Lets the user manually map every source column
- Supports a single signed amount column or separate debit/credit columns
- Supports source type labels, sign-based type detection, or forcing all rows to income/expense
- Supports common date formats and Unix timestamps
- Handles UTF-8, GB18030, Big5, and other common CSV encodings
- Can filter by currency and assign a fallback category
- Can create missing categories automatically
- Previews normalized data before database writes
- Detects invalid rows and exact duplicate transactions
- Exports rejected rows for correction
- Inserts in batches and never overwrites or deletes existing records

## Gemini AI import wizard

The AI import page is designed for irregular or mixed documents that cannot be handled reliably by fixed column mapping.

- Uses the stable `gemini-3.6-flash` model through the current Google Gen AI SDK
- Accepts multiple CSV, XLSX, XLS, JSON, TXT, Markdown, PDF, image, and DOCX files
- Splits large tables and text into controlled batches before model calls
- Uses structured JSON output validated by Pydantic
- Extracts date, merchant/item, category, cash-flow type, amount, note, currency, confidence, and source reference
- Explicitly instructs the model not to turn balances, subtotals, totals, budgets, or duplicated headers into transactions
- Supports Malaysian day-first dates, a default currency, existing-category enforcement, and custom user instructions
- Marks missing dates, ambiguous types, low-confidence records, and potential duplicates for human review
- Keeps low-confidence and duplicate rows unselected by default
- Provides an editable review table and a downloadable AI-normalized CSV
- Requires final confirmation before inserting any row
- Imports only selected valid records and can create confirmed new categories
- Sends uploaded content to the Gemini API only after the user clicks the processing button

## Report design

The original quick-reading charts are retained in **Quick overview**:

- January-to-December spending bars
- Category donut chart
- Daily spending bars
- Monthly category ranking
- Spending calendar

Additional report layers include:

- Income, expense, and balance trend
- Cumulative spending compared with the previous year
- Monthly savings rate
- Monthly category composition
- Daily spending stacked by category
- Weekday spending pattern
- Transaction-size distribution
- Category amount, frequency, and average transaction size
- Twelve-month trend for an individual category
- High-spending items
- Statistical high-value transaction prompts
- Repeated-item / possible recurring expense detection
- Duplicate, blank-name, and invalid-amount data checks

All spending bar charts use a zero-based Y-axis. Annual charts always include all 12 months, including months with no spending.

## Safety

- No database migration is included.
- Root `app.py` and root `requirements.txt` are not modified.
- V2 writes to the same Supabase data as the old site, so test deletion, category merging, and imports carefully.
- Both import modes only add records. A failed multi-batch import may have inserted earlier batches, so rerun duplicate detection before retrying.
- AI extraction can be wrong. Review every low-confidence, missing-date, or ambiguous row before selecting it.
- Uploaded content processed by the AI import page is sent to the Google Gemini API; remove unnecessary sensitive identifiers first.
- Keep Pull Request #1 as a draft until the separate V2 deployment has been tested.
