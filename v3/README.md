# WY Wallet V3

Candidate build: `2026.09.01-v3.2.1 · v3-release-candidate-r1`

Stable live deployment remains on `agent/wy-wallet-v3` until the candidate release gate is fully green. Candidate development happens on `agent/wy-wallet-v3-finalize`.

Deployment entrypoint: `v3/app.py`.

Required service secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`.

For the final database-security cutover, `SUPABASE_KEY` must contain a **backend-only** Supabase secret key (`sb_secret_...`) or the legacy `service_role` key before `v3/supabase/rls_cutover.sql` is applied. Never expose that key in browser/client code.

For a public Streamlit URL, configure `WEB_ACCESS_PASSWORD`. Only when Streamlit itself already enforces Private access should `ALLOW_UNPROTECTED_ACCESS = true` be used. If neither is configured, V3 fails closed before loading ledger data.

## V3.2.1 candidate architecture

- V3 executes from its own `v3/wywallet` package; it no longer runs the V2 core.
- The entrypoint no longer mutates page functions with runtime `setattr` overrides.
- Startup renders an explicit database-loading state and shows a diagnostic page if Supabase fails instead of leaving a blank screen.
- Dashboard / Reports / AI stop authoritative calculations if the interactive ledger is truncated above the 100,000-row safety limit.
- Receipt flow has a dedicated V3 page with mobile-first card editing.
- Receipt identity is whole-receipt + line-level: a receipt number is preferred when available, otherwise line content is included in the fingerprint; identical repeated receipt lines receive distinct line IDs.
- Final receipt save performs a fresh database check and never silently writes a partial receipt.
- AI finance numbers remain Python-authoritative; Gemini 3.7 Flash handles language understanding and receipt extraction.
- Refund reduces net spending rather than inflating logical V3 income.
- Historical month-end forecast uses a median remaining-spend estimate and exposes an uncertainty range.
- Spreadsheet exports neutralize formula-like external text.
- Interactive reads use the database ledger revision as a lightweight freshness check. A normal rerun reuses the current session snapshot when the revision is unchanged instead of retransferring the full ledger.
- A successful single-transaction insert can patch the session snapshot locally only when the database revision proves no concurrent writer intervened; otherwise it falls back to an authoritative full refresh.

## Database finalize state — 2026-09-02

Already present in the connected WY Wallet Supabase project:

- atomic insert RPC with idempotent `client_token` protection;
- optimistic-concurrency update/delete RPCs using `updated_at`;
- atomic category-merge RPC;
- transaction-consistent snapshot and backup-snapshot RPCs;
- database ledger revision + revision bump triggers;
- transaction metadata / receipt / client-token columns and supporting indexes;
- CHECK / UNIQUE constraints used by the V3 release paths.

Already hardened without changing the live app access model:

- low-privilege roles can no longer directly execute trigger-only metadata/timestamp helper functions;
- default privileges for **future** public tables, sequences and functions no longer auto-grant access to `anon` / `authenticated`;
- the internal `private` schema is not usable by `anon` / `authenticated`.

Still intentionally pending to avoid breaking the current stable deployment:

- `public.transactions` and `public.categories` still need their final RLS cutover because the deployed Streamlit `SUPABASE_KEY` privilege level cannot be read from repository code;
- after the Streamlit backend is confirmed to use a secret/service-role key, apply `v3/supabase/rls_cutover.sql`, then rerun Supabase security advisors and the V3 smoke tests;
- GitHub Branch Protection remains an owner-managed action and is not changed by the finalize branch work.

## Release policy

`v3/RELEASE_GATE.md` is the fixed definition of a release blocker. New audits should not reopen the release for theoretical boundaries or optional UI polish. The live branch is advanced only after Python 3.12 and 3.14 release-gate jobs both pass against the actual V3 entrypoint.
