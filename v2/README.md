# WY Wallet V2

This directory contains the isolated V2 application. The existing production entry point in the repository root remains unchanged.

## Deploy as a separate Streamlit app

- Repository: `wuyaoenterprise/WY-Wallet`
- Branch: `agent/wy-wallet-v2-redesign`
- Main file: `v2/app.py`
- Copy the existing `SUPABASE_URL`, `SUPABASE_KEY`, and `GOOGLE_API_KEY` values into the new Streamlit app secrets.

V2 continues to use the existing Supabase `transactions` and `categories` tables. Changes made in either site therefore affect the same transaction data.

## Current V2 design

- Sidebar navigation and compact dashboard
- Current-month income, expenses, balance, daily average, and comparison with the previous month
- Professional transaction table with search, filters, sorting, row selection, editing, safe deletion, and one-step undo
- Category creation integrated directly into the add and edit transaction flows
- Receipt recognition with editable results
- Reports organized as yearly trend, monthly detail, and category/item analysis
- Yearly spending chart always shows January through December and starts the Y-axis at RM 0
- Daily monthly chart also starts at RM 0
- Category ranking uses readable horizontal bars instead of relying on pie charts
- AI analysis receives summarized statistics rather than the complete yearly ledger
- Excel and CSV export
- CSV and Excel import with duplicate detection
- Category rename/merge workflow that also updates old transactions

## Safety

- No database migration is included.
- Root `app.py` and root `requirements.txt` are not modified.
- Keep Pull Request #1 as a draft until the separate V2 deployment has been tested.
