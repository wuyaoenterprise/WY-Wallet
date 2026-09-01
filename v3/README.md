# WY Wallet V3

Current build: `2026.09.01-v3.1.1 · v3-final-audit-r2`

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

V3 is fail-closed for financial-data access. Configure exactly the protection appropriate for the deployment.

### Recommended for a public Streamlit URL

Set `WEB_ACCESS_PASSWORD` in Streamlit Secrets. Do not commit the password to GitHub. Password sessions expire after 30 minutes of inactivity and the V3 sidebar includes a manual session-lock action.

### If Streamlit itself already enforces private platform access

Set:

```toml
ALLOW_UNPROTECTED_ACCESS = true
```

Only use this override when the hosting platform already prevents unauthorized users from opening the app.

If neither option is configured, V3 intentionally stops at a security-setup screen instead of exposing the ledger.

## V3.1.1 accounting and reliability behavior

- Refunds are logical `Refund` transactions and reduce net spending instead of inflating income inside V3.
- Shared-table refund storage uses a positive-amount marker representation, so V3 does not depend on the web Supabase schema allowing negative amounts. Older negative-expense refunds remain readable.
- Future dates are excluded from posted-ledger analytics.
- Transaction reads use ID keyset pagination, avoiding offset-pagination drift during long reads/backups.
- V3 fragments reload the shared ledger snapshot internally instead of holding the parent run's DataFrame arguments.
- All app writes invalidate the shared ledger cache and any same-session prepared backup snapshot; prepared backup bundles also carry a ledger signature so a later cross-session ledger change invalidates them before another download rerun.
- Category merge verifies the move and attempts rollback if a partial move fails; rollback only touches rows that are still at the merge target so a newer concurrent category edit is not overwritten.
- Spreadsheet exports neutralize formula-like external text.

## Forecasting and reports

- Month-end forecast is history-aware: current actual spending plus the recent historical average of spending after the same day-of-month. It does not multiply month-start rent/car-loan payments by the number of days in the month.
- Current-month bars are marked with `*` as incomplete.
- Current-year monthly average uses completed months rather than treating the newly started month as complete.
- Refunds reduce category, monthly and macro net spending.
- Negative net-expense categories are surfaced as net-refund reconciliation rows instead of silently disappearing behind positive-only quick charts.

## AI

Gemini 3.7 Flash is used for language understanding, structured planning, receipt extraction and explanation. Authoritative finance values are calculated in local Python.

Supported local aggregations include:

- total amount
- transaction count
- average per transaction
- average per calendar day
- average per completed calendar month for an in-progress current-year range
- median
- largest single transaction, including its date/item/category
- smallest single transaction, including its date/item/category

Comparisons support previous month, previous equal-length period, previous-year same period, and an explicit custom comparison range such as `8月跟6月比`. The comparison target is persisted in conversation state for short follow-ups such as `差多少百分比？`.

The planner no longer sends thousands of merchant/item candidate names for every question. Subject text is interpreted by Gemini and resolved against the ledger locally. Simple amount/count/list questions skip the second AI explanation call when no explanation is needed.

## Receipt recognition

- Gemini 3.7 structured extraction
- image size guard
- mandatory human date confirmation when unreadable
- duplicate check before and immediately before save
- user override for legitimate identical transactions
- receipt-level tax/service/discount materialized as editable ledger rows
- semantic receipt fingerprint based on extracted receipt contents rather than image bytes, reducing duplicate adjustment rows when the same physical receipt is photographed again
- if the final fresh duplicate check changes the candidate set, V3 saves nothing and requires the user to rerun/reconfirm so the reconciled total always matches the rows actually inserted
- local total reconciliation before saving

## Performance

V3 uses Streamlit fragments for page-local interactions and one shared ledger cache in the database layer. Fragment entrypoints reload the shared cache internally, preserving the faster interaction model without freezing a parent-run transaction DataFrame.

## Validation

GitHub Actions runs on both pull requests and pushes to `agent/wy-wallet-v3`, validates Python 3.12 and 3.14, and covers dependency checks, Python compilation, stale V2-branding guards, deprecated API guards, finance regressions, V3 override regressions, receipt regressions, access-gate AppTests, V3 entrypoint execution, and module smoke imports.

## Remaining external verification

The application-layer issues identified in the V3.1 audit are addressed in V3.1.1. The one remaining external boundary is the legacy Web Supabase project's own security configuration: its RLS policies, anon-key permissions, database constraints and indexes cannot be verified or changed from this repository because that Supabase project is not connected to the available management tooling. Review those settings in the Supabase project before treating the database layer as independently hardened.
