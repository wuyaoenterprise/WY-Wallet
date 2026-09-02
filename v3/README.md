# WY Wallet V3

Current application version: `v3.2.3`

Stable deployment branch: `agent/wy-wallet-v3`.
Development/finalization branch: `agent/wy-wallet-v3-finalize`.
Deployment entrypoint: `v3/app.py`.

Required service secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`.
For a public Streamlit URL, configure `WEB_ACCESS_PASSWORD`. Only when Streamlit itself already enforces Private access should `ALLOW_UNPROTECTED_ACCESS = true` be used.

## V3.2.3 product state

- Dashboard, Transactions, Reports, AI and Settings use dedicated V3 page modules.
- Dashboard month windows are anchored to Malaysia current month, so the current partial month is always represented.
- Primary KPI rows use equal-width cards with normalized heights.
- Manual password entry supports Enter-to-submit.
- Manual transaction entry uses an idempotent insert token and shows a non-blocking warning for exact duplicate-looking entries.
- Frequently used categories are ranked first in entry/edit/filter flows.
- Transaction table/card views use clamped pagination, and explicit refresh clears both legacy and snapshot caches.
- Update/delete and invalid-row repair use database optimistic-concurrency checks.
- Reports avoid fake prior-year zero lines, use a complete Top-8-plus-other category pie denominator, and limit tall category charts while retaining the complete table.
- AI finance numbers remain Python-authoritative; Gemini handles query understanding, explanations and receipt extraction. Chat immediately renders the user's submitted message.
- Receipt card/table editors share one draft state. OCR future dates are replaced with today only as an unconfirmed draft and require manual date confirmation.
- Receipt writes preserve structured `receipt_id` and `flow_subtype`; refunds/discounts remain logical refunds rather than income.
- Final receipt save performs a fresh database duplicate check and never silently writes a partial receipt.
- Full backup uses a single PostgreSQL MVCC snapshot and exports structured receipt/concurrency metadata.
- UI muted text is derived from Streamlit theme text color for better light/dark readability.
- Displayed version labels use only the compact `v3.x.x` format.

## Data freshness and latency

- Interactive reads use the database ledger revision as a lightweight freshness check.
- Normal reruns reuse the current session snapshot when the database revision is unchanged instead of retransferring the full ledger.
- A successful single-transaction insert patches the session snapshot locally only when the revision proves no concurrent writer intervened; otherwise the next rerun performs an authoritative refresh.
- The app deliberately avoids nested `st.fragment` navigation chains because the live latency hotfix showed they could make page switching feel stuck.

## Database state

Already present in the connected WY Wallet Supabase project:

- atomic/idempotent single-transaction insert RPC;
- optimistic-concurrency update/delete RPCs;
- atomic category merge RPC;
- transaction-consistent snapshot and backup snapshot RPCs;
- database ledger revision and revision bump triggers;
- structured transaction metadata columns and supporting indexes/constraints.

Database security cutover is tracked separately from product/functionality work. `public.transactions` and `public.categories` RLS remains intentionally pending until the Streamlit backend key is confirmed suitable for the prepared `v3/supabase/rls_cutover.sql` migration.

## Release policy

`v3/RELEASE_GATE.md` defines the release blockers. The deployment branch is advanced only after both Python 3.12 and 3.14 jobs pass compile, obsolete-pattern rejection, V3 regression tests, and runtime imports against the actual V3 entrypoint.
