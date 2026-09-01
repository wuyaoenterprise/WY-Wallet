# WY Wallet V3

Candidate build: `2026.09.01-v3.2.1 · v3-release-candidate-r1`

Stable live deployment remains on `agent/wy-wallet-v3` until the candidate release gate is fully green. Candidate development happens on `agent/wy-wallet-v3-finalize`.

Deployment entrypoint: `v3/app.py`.

Required service secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`.

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

## Release policy

`v3/RELEASE_GATE.md` is the fixed definition of a release blocker. New audits should not reopen the release for theoretical boundaries or optional UI polish. The live branch is advanced only after Python 3.12 and 3.14 release-gate jobs both pass against the actual V3 entrypoint.

## Owner / infrastructure actions

Repository code cannot verify the legacy Web Supabase project's RLS, role permissions, deployed-key privilege level, indexes/constraints, atomic category-merge RPC, optimistic row versioning or database-level backup revision. Those require project-owner access to the actual Supabase project.

GitHub branch protection and Streamlit platform privacy also require account-level administrative actions.
