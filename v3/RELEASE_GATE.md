# WY Wallet V3 Release Gate

This document is the fixed release standard for V3. A later audit must not call the release "unfinished" merely because it finds theoretical edge cases or optional polish. A release blocker must be reproducible and belong to one of the categories below.

## Release blockers

All of these must be green before `agent/wy-wallet-v3` is advanced:

1. The actual `v3/app.py` starts in Streamlit AppTest without an exception.
2. The repository root `app.py` is a retired fail-closed guard only, and root `requirements.txt` is byte-identical to `v3/requirements.txt`; an accidental root deployment must not revive the legacy Smart Asset Pro runtime.
3. Missing access configuration fails closed before ledger data is loaded.
4. A configured password gate is shown before ledger data is loaded.
5. Supabase read failure produces a visible diagnostic page instead of a blank/hanging UI.
6. Dashboard, Reports and AI never present truncated (>100,000-row) data as complete totals.
7. Expense / Income / Refund accounting regressions pass.
8. AI authoritative totals, complete-month monthly average, max transaction and custom comparison regressions pass.
9. Receipt total reconciliation and final fresh duplicate checking never partially write a save operation.
10. Whole-receipt identity, order-independent receipt-line identity and partial-receipt completion regressions pass.
11. Editing a receipt-linked transaction detaches stale receipt provenance when date/item/type/amount changes, while category/note-only edits preserve it.
12. Future-dated ledger rows are rejected.
13. Spreadsheet export formula-injection guards pass.
14. Runtime code contains no V2 dependency, V3 override layer, source rewriting or deprecated Streamlit width API.
15. Python 3.12 and 3.14 release-gate jobs both pass.

## Application behavior considered intentional

These are not release defects unless the documented behavior itself regresses:

- Refund reduces net expense and does not increase logical V3 income.
- Anomaly/recurring detection examines gross expense transactions; it is transaction-behavior analysis, not net-expense accounting.
- Receipt tax, service charge and discount use structured flow subtypes; receipt discount remains a logical Refund and reduces net expense.
- A first tracking month that begins after day 1 is not treated as a complete monthly-average observation once later complete months exist.
- The interactive UI refuses authoritative totals once its safety row limit is exceeded; full export remains the escape path.
- Category merge uses the database atomic merge RPC; update/delete and invalid-row repair use optimistic-concurrency checks.

## External infrastructure items

These cannot be truthfully marked fixed from repository code alone and must not be repeatedly rediscovered as new application bugs:

1. Legacy Web Supabase RLS and role permissions.
2. Whether the deployed `SUPABASE_KEY` is an anon/publishable key rather than an elevated backend key.
3. Streamlit platform privacy and which old/independent deployments are still running.
4. GitHub/Streamlit deployment branch protection and administrative settings.

These items require the project owner's Supabase or GitHub/Streamlit administrative access.

## Non-blocking polish

Additional merchant aliases, additional forecasts, new AI intents, animations and personal layout preferences are product enhancements. They do not reopen a stable release unless they cause one of the blockers above to fail.
