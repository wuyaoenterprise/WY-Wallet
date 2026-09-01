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

## V3.1.1 final audit fixes

- Fragment pages reload the shared ledger snapshot internally, avoiding parent-run stale DataFrames while preserving fast local reruns.
- Current-year AI `average_month` excludes an incomplete current month when completed months exist, matching the report definition.
- Largest/smallest transaction queries return the concrete transaction date, item and category.
- Highest/lowest month output respects aggregation units, so count queries display `笔` rather than `RM`.
- Custom comparison targets persist in conversation state for follow-up questions such as `差多少百分比？`.
- Gemini planning no longer receives thousands of ledger merchant/item candidate names on every question; exact subject matching happens locally.
- Simple amount/count/list questions skip the second Gemini explanation call unless an explanation is actually needed.
- Receipt adjustment fingerprints are based on extracted receipt contents rather than image bytes, reducing duplicate tax/service/discount rows when the same physical receipt is photographed again.
- If the final fresh duplicate check changes the rows that would be saved, V3 saves nothing and requires reconciliation/confirmation again instead of silently saving a partial receipt.
- Negative net-expense categories are surfaced as net-refund reconciliation rows rather than disappearing from positive-only quick charts.
- Prepared backup bundles carry a ledger signature and are invalidated before a later download rerun if another session changed the ledger.
- Category-merge rollback only changes rows that are still at the merge target, reducing the chance of overwriting a newer concurrent category edit.
- Password sessions expire after 30 minutes of inactivity and users can manually lock the current session.
- CI runs on both pull requests and pushes to the V3 branch, on Python 3.12 and 3.14, and includes V3-specific override regressions.

## Existing V3 accounting behavior

- Refunds are logical `Refund` transactions and reduce net spending instead of inflating income inside V3.
- Shared-table refund storage uses a positive-amount marker representation, so V3 does not depend on the web Supabase schema allowing negative amounts. Older negative-expense refunds remain readable.
- Future dates are excluded from posted-ledger analytics.
- Transaction reads use ID keyset pagination.
- Spreadsheet exports neutralize formula-like external text.
- Month-end forecast is history-aware rather than linearly multiplying month-start fixed costs.
- Current-month bars are marked as incomplete and current-year report averages use completed months.
- Gemini 3.7 Flash handles language/structured extraction; authoritative finance values are calculated in Python.

## Remaining external verification

The application-layer issues identified in the V3.1 audit are addressed in V3.1.1. The one remaining external boundary is the legacy Web Supabase project's own security configuration: its RLS policies, anon-key permissions, database constraints and indexes cannot be verified or changed from this repository because that Supabase project is not connected to the available management tooling. Review those settings in the Supabase project before treating the database layer as independently hardened.
