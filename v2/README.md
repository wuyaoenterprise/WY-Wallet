# WY Wallet V2

This directory contains the isolated Streamlit V2 application. Root production `app.py` and root `requirements.txt` remain unchanged.

## Deployment

- Repository: `wuyaoenterprise/WY-Wallet`
- Branch: `agent/wy-wallet-v2-redesign`
- Main file: `v2/app.py`
- Secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `GOOGLE_API_KEY`
- Timezone: `Asia/Kuala_Lumpur`

V2 continues using the existing Supabase `transactions` and `categories` tables, so changes in the old site and V2 affect the same data.

## Stable architecture

V2 no longer rewrites Python source at runtime and no longer uses `exec`, Streamlit monkey-patching, or Gemini model monkey-patching.

- `v2/app.py` — tiny stable entry point
- `v2/wywallet/config.py` — constants, MYR, Malaysia timezone, Gemini 3.7 model ID
- `v2/wywallet/db.py` — paginated reads, validation, writes, categories, cache invalidation
- `v2/wywallet/analytics.py` — deterministic finance calculations
- `v2/wywallet/ai.py` — Google GenAI SDK, structured finance query planning, receipt extraction
- `v2/wywallet/ui.py` — safe HTML helpers and fixed Plotly configuration
- `v2/wywallet/web.py` — main UI
- `v2/pages/1_📷AI收据识别.py` — dedicated receipt workflow
- `v2/tests/test_core.py` — finance and validation regression tests

Legacy `v2/app_core.py` and `v2/app_rich.py` were physically removed, so deleted import features and old AI model calls cannot reappear through an incorrect entry point.

## Data correctness and safety

- Supabase transaction reads paginate in 1,000-row batches up to 100,000 rows.
- All new/edited transactions require a valid date, non-empty item/category, `Expense`/`Income` type, and amount greater than zero.
- Search is literal, not regular-expression based.
- Categories shown in filters are the union of the category table and categories actually present in historical transactions.
- Category merge is ordered to avoid data loss: transactions are moved before the source category is removed, and the result is verified.
- Database errors are cleared after a successful read.
- A manual refresh button invalidates shared caches.
- Backups include `Transactions`, `Categories`, and `Metadata` sheets.
- Historical data import has been physically removed from V2.

## AI

All AI calls use stable `gemini-3.7-flash` through `google-genai`.

Finance chat no longer sends a duplicated full ledger on every question. Gemini first converts the conversation into a structured query plan (topic, metric, year, month range and exact matching ledger labels). Pandas then calculates authoritative amounts locally. Gemini receives only the calculated result for the final natural-language answer.

Conversation state stores the resolved subject and time scope, so follow-ups such as `1到8月分别多少？`, `哪个月最高？`, and `那2025呢？` preserve the previous topic unless the user explicitly changes it. Chat state is reset when the selected year or underlying ledger signature changes.

Ledger strings are explicitly treated as untrusted data in AI system instructions to reduce prompt-injection risk.

Receipt recognition uses structured output, human review, final validation, duplicate checks after editing, duplicate detection within the candidate batch, a fresh duplicate check immediately before insert, and shared cache invalidation.

## Reports and UX

- Locked Plotly charts: no accidental drag/zoom/double-click reset or toolbar.
- Income/expense/balance semantic colors and consistent styling.
- Mobile-friendly transaction card mode in addition to the desktop table.
- Current-year monthly average includes elapsed months even when a month has zero spending; past years divide by all 12 months.
- Savings rate displays `N/A` when no income exists.
- Weekday analysis uses average spending per occurrence of each weekday instead of raw weekday totals.
- Recurring-expense detection combines month coverage, amount stability and time cadence so frequent daily purchases are not automatically labelled subscriptions.
- Anomaly detection compares transactions within their own category.
- User transaction values rendered in custom HTML are escaped.

## Validation

GitHub Actions tests both Python 3.12 and 3.14. CI installs all dependencies, compiles V2, rejects the legacy Gemini SDK/runtime source rewriting, runs core unit tests, and smoke-imports the application modules.

The stabilization workflow currently passes on both supported Python versions.
