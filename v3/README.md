# WY Wallet V3

Current application version: `v3.2.5`

Stable deployment branch: `agent/wy-wallet-v3`.
Development/finalization branch: `agent/wy-wallet-v3-finalize`.
Deployment entrypoint: `v3/app.py`.
The repository-root `app.py` is intentionally a retired fail-closed guard; it must never be used as the production entrypoint.

Required service secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`.
For a public Streamlit URL, configure `WEB_ACCESS_PASSWORD`. Only when Streamlit itself already enforces Private access should `ALLOW_UNPROTECTED_ACCESS = true` be used.

## V3.2.5 product state

- Dashboard, Transactions, Reports, AI and Settings use dedicated V3 page modules.
- The old root Smart Asset Pro runtime has been retired. Root dependencies are pinned identically to V3, and CI rejects any reintroduction of the old runtime or dependency set.
- Dashboard month windows are anchored to Malaysia current month, so the current partial month is always represented.
- Primary KPI rows use equal-height cards; on narrow screens metric columns wrap into a readable two-per-row layout instead of compressing five cards across the phone width.
- Plotly chart containers allow label overflow with larger safe margins to reduce edge-label clipping.
- Manual password entry supports Enter-to-submit.
- Manual transaction entry uses an idempotent insert token, prevents zero-value input at the UI layer, enforces visible text-length limits, and shows a non-blocking warning for exact duplicate-looking entries.
- Frequently used categories are ranked first in entry/edit/filter flows.
- Transaction table/card views use clamped pagination; large-ledger truncation is explicitly disclosed instead of looking like a complete search; explicit refresh clears both legacy and snapshot caches.
- Update/delete and invalid-row repair use database optimistic-concurrency checks.
- Delete undo uses its own idempotent restore token rather than suppressing a valid undo just because another visually identical transaction exists.
- Receipt-linked transactions preserve receipt provenance for category/note-only edits, but changing date/item/type/amount explicitly detaches the stale Receipt ID; receipt-specific subtypes are also detached safely.
- Reports avoid fake prior-year zero lines, do not call an RM0 month the “highest spending month”, use a complete Top-8-plus-other category pie denominator, limit tall category charts while retaining the complete table, and scope invalid-row quality metrics to the selected year.
- Historical monthly averages use complete tracked calendar months: if the ledger starts after day 1, that first partial month is excluded once later complete months exist. The same rule is used by AI monthly-average queries.
- Recurring-expense detection keeps item/category pairs separate so identical item names in different categories are not merged into one pattern.
- AI finance numbers remain Python-authoritative; Gemini handles query understanding, explanations and receipt extraction. Chat immediately renders the user's submitted message.
- Receipt card/table editors share one draft state. OCR future dates are replaced with today only as an unconfirmed draft and require manual date confirmation.
- Receipt confirmation controls are scoped to the current image so confirmations cannot leak from one receipt to another.
- Receipt root identity is generated after human edits from the complete final draft. Receipt line IDs use order-independent semantic fingerprints, while truly identical repeated lines receive distinct occurrence suffixes.
- A previously partial receipt can be scanned again to safely add only its missing semantic lines. Existing lines are duplicate-blocked; final save still performs a fresh snapshot check and aborts the entire save if a new duplicate appears concurrently.
- A fully saved receipt remains blocked by default; explicit whole-receipt duplicate confirmation creates a separate duplicate root rather than corrupting the original provenance.
- Receipt writes preserve structured `receipt_id` and `flow_subtype`; refunds/discounts remain logical refunds rather than income.
- Legacy internal refund/receipt note markers are decoded before invalid-row repair so repair UI shows the logical refund type and user-visible note rather than internal metadata.
- Full backup uses a single PostgreSQL MVCC snapshot and exports structured receipt/concurrency metadata.
- Dashboard and transaction tabular views use responsive dataframes rather than fixed tables for better narrow-screen behavior.
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

`v3/RELEASE_GATE.md` defines the release blockers. The deployment branch is advanced only after both Python 3.12 and 3.14 jobs pass root-entrypoint hygiene, dependency alignment, compile, obsolete-pattern rejection, V3 regression tests and runtime imports against the actual V3 entrypoint.
