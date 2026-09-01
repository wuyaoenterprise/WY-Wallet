# WY Wallet V3 Release Gate

This document is the fixed release standard for V3. A later audit must not call the release "unfinished" merely because it finds theoretical edge cases or optional polish. A release blocker must be reproducible and belong to one of the categories below.

## Release blockers

All of these must be green before `agent/wy-wallet-v3` is advanced:

1. The actual `v3/app.py` starts in Streamlit AppTest without an exception.
2. Missing access configuration fails closed before ledger data is loaded.
3. A configured password gate is shown before ledger data is loaded.
4. Supabase read failure produces a visible diagnostic page instead of a blank/hanging UI.
5. Dashboard, Reports and AI never present truncated (>100,000-row) data as complete totals.
6. Expense / Income / Refund accounting regressions pass.
7. AI authoritative totals, monthly average, max transaction and custom comparison regressions pass.
8. Receipt total reconciliation and final fresh duplicate checking never partially save a receipt.
9. Whole-receipt identity and receipt-line identity regressions pass.
10. Future-dated ledger rows are rejected.
11. Spreadsheet export formula-injection guards pass.
12. Runtime code contains no V2 dependency, V3 override layer, source rewriting or deprecated Streamlit width API.
13. Python 3.12 and 3.14 release-gate jobs both pass.

## Application behavior considered intentional

These are not release defects unless the documented behavior itself regresses:

- Refund reduces net expense and does not increase logical V3 income.
- Anomaly/recurring detection examines gross expense transactions; it is transaction-behavior analysis, not net-expense accounting.
- Receipt discount reduces net expense. Until the database has a refund subtype field, the UI may group it with refund/discount wording rather than claim it is a customer refund.
- The interactive UI refuses authoritative totals once its safety row limit is exceeded; full export remains the escape path.
- App-level category merge uses verification and rollback because the current legacy database does not expose an atomic RPC to this repository.

## External infrastructure items

These cannot be truthfully marked fixed from repository code alone and must not be repeatedly rediscovered as new application bugs:

1. Legacy Web Supabase RLS and role permissions.
2. Whether the deployed `SUPABASE_KEY` is an anon/publishable key rather than an elevated service key.
3. Database indexes and CHECK/UNIQUE constraints.
4. Atomic category-merge RPC / transaction support.
5. Optimistic concurrency (`updated_at` or row version) for single-statement compare-and-update/delete.
6. A database-level ledger revision or snapshot RPC for perfect cross-client backup freshness.
7. Streamlit platform privacy / deployment branch protection.

These items require the project owner's Supabase or GitHub/Streamlit administrative access.

## Non-blocking polish

Dark-mode contrast tweaks, chart color refinements, additional merchant aliases, additional forecasts, new AI intents, animations and layout preferences are product enhancements. They do not reopen a stable release unless they cause one of the blockers above to fail.
