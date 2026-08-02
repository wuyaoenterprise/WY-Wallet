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
- The entry point uses `runpy` so the implementation executes again on every Streamlit rerun.
- Transaction SELECT queries are automatically paginated in 1,000-row batches so historical records are not hidden by the Supabase per-request row limit.
- `.github/workflows/v2-syntax-check.yml` installs V2 dependencies and compiles all remaining Python files.

## Current V2 design

- Sidebar navigation and compact dashboard
- Current-month income, expenses, balance, daily average, month comparison, and projected month-end spending
- Professional transaction table with search, filters, sorting, row selection, editing, safe deletion, and one-step undo
- Category creation integrated directly into add, edit, and receipt workflows
- Receipt recognition with editable results
- AI analysis receives summarized statistics rather than the complete yearly ledger
- Excel and CSV backup export
- Category rename/merge workflow that also updates old transactions
- No data-import page or upload interface

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
- V2 writes to the same Supabase data as the old site, so test deletion and category merging carefully.
- Keep Pull Request #1 as a draft until the separately deployed V2 site has been tested.
