# WY Wallet V3

Current build: `2026.09.01-v3.1.0 · v3-final-hardening-r1`

## Streamlit deployment

- Repository: `wuyaoenterprise/WY-Wallet`
- Branch: `agent/wy-wallet-v3`
- Main file: `v3/app.py`
- Requirements: `v3/requirements.txt`
- Timezone: `Asia/Kuala_Lumpur`
- AI model: `gemini-3.7-flash`

## Required Secrets

The app requires the existing service secrets:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GOOGLE_API_KEY`

V3 is fail-closed for financial-data access. Configure exactly the protection appropriate for the deployment:

### Recommended for a public Streamlit URL

Set `WEB_ACCESS_PASSWORD` in Streamlit Secrets. Do not commit the password to GitHub.

### If Streamlit itself already enforces private platform access

Set:

```toml
ALLOW_UNPROTECTED_ACCESS = true
```

Only use this override when the hosting platform already prevents unauthorized users from opening the app.

If neither option is configured, V3 intentionally stops at a security-setup screen instead of exposing the ledger.

## V3.1 accounting and reliability behavior

- Refunds are logical `Refund` transactions and reduce net spending instead of inflating income inside V3.
- Shared-table refund storage uses a positive-amount marker representation, so V3 does not depend on the web Supabase schema allowing negative amounts. Older negative-expense refunds remain readable.
- Future dates are excluded from posted-ledger analytics.
- Transaction reads use ID keyset pagination, avoiding offset-pagination drift during long reads/backups.
- All app writes invalidate the shared ledger cache and any prepared backup snapshot.
- Category merge verifies the move and attempts rollback if a partial move fails.
- Spreadsheet exports neutralize formula-like external text.

## Forecasting and reports

- Month-end forecast is history-aware: current actual spending plus the recent historical average of spending after the same day-of-month. It does not multiply month-start rent/car-loan payments by the number of days in the month.
- Current-month bars are marked with `*` as incomplete.
- Current-year monthly average uses completed months rather than treating the newly started month as complete.
- Refunds reduce category, monthly and macro net spending.

## AI

Gemini 3.7 Flash is used only for language understanding, structured planning, receipt extraction and explanation. Authoritative finance values are calculated in local Python.

Supported local aggregations include:

- total amount
- transaction count
- average per transaction
- average per calendar day
- average per calendar month
- median
- largest single transaction
- smallest single transaction

Comparisons support previous month, previous equal-length period, previous-year same period, and an explicit custom comparison range such as `8月跟6月比`.

AI explanations receive the locally calculated total, monthly series, comparison result, matching scope and top contributors without receiving the full ledger unnecessarily.

## Receipt recognition

- Gemini 3.7 structured extraction
- image size guard
- mandatory human date confirmation when unreadable
- duplicate check before and immediately before save
- user override for legitimate identical transactions
- receipt-level tax/service/discount materialized as editable ledger rows
- receipt fingerprint on generated adjustment rows to avoid false duplicate matches between separate receipts
- local total reconciliation before saving

## Performance

V3 uses Streamlit fragments for page-local interactions and one shared ledger cache in the database layer. The former extra per-session DataFrame cache was removed to prevent cross-session stale views.

## Validation

GitHub Actions validates Python 3.12 and 3.14 and currently covers dependency checks, Python compilation, stale V2-branding guards, deprecated API guards, finance regressions, receipt regressions, access-gate AppTests, V3 entrypoint execution, and module smoke imports.
