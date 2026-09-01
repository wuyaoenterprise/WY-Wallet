# WY Wallet V3

Current build: `2026.09.01-v3.1.1 · v3-final-audit-r2`

Deployment: `wuyaoenterprise/WY-Wallet` → branch `agent/wy-wallet-v3` → main file `v3/app.py`.

Required service secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`.

For access protection, configure `WEB_ACCESS_PASSWORD` for a public Streamlit URL. If Streamlit itself already enforces private platform access, explicitly set `ALLOW_UNPROTECTED_ACCESS = true`. If neither is configured, V3 fails closed before loading ledger data.

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

Refunds reduce net spending rather than inflating income inside V3. Shared-table refund storage uses a positive-amount marker representation, future dates are excluded from posted analytics, transaction reads use ID keyset pagination, spreadsheet exports neutralize formula-like external text, month-end forecasts use recent historical remaining-day patterns, and authoritative finance numbers are calculated locally in Python while Gemini 3.7 Flash handles language understanding and receipt extraction.

## Remaining external verification

The application-layer issues identified in the V3.1 audit are addressed in V3.1.1. The one remaining external boundary is the legacy Web Supabase project's own security configuration: its RLS policies, anon-key permissions, database constraints and indexes cannot be verified or changed from this repository because that Supabase project is not connected to the available management tooling.
