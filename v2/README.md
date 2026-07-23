# WY Wallet V2

This folder contains an isolated Streamlit V2 entry point. The original root `app.py` and existing website remain unchanged.

## Deploy as a second Streamlit app

1. Create a new app in Streamlit Community Cloud.
2. Select repository `wuyaoenterprise/WY-Wallet`.
3. Select branch `agent/wy-wallet-v2-redesign` while testing.
4. Set the main file path to `v2/app.py`.
5. Copy the same secrets used by the original app:

```toml
SUPABASE_URL = "..."
SUPABASE_KEY = "..."
GOOGLE_API_KEY = "..."
```

The V2 app reads and writes the existing Supabase `transactions` and `categories` tables. It performs no database migration.

## Changes included

- New dashboard with current-month overview and month-over-month comparison
- Responsive transaction cards
- Search, year/month/type filters, and pagination
- Delete confirmation and one-step restore for the last deleted transaction
- Safer category deletion confirmation
- Existing manual entry and AI receipt recognition retained
- Monthly calendar, category charts, and annual trend retained
- AI chat sends summary statistics instead of the complete yearly transaction CSV
- Excel and CSV export
- Additive CSV/Excel import with preview and confirmation
- Clearer database error reporting

## Safety notes

- Import only adds records and can create duplicates.
- Restoring a deleted record creates a new database ID while preserving its content.
- The original root application and root dependency file are intentionally untouched.
