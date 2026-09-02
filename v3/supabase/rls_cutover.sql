-- WY Wallet V3: final Supabase RLS cutover
--
-- IMPORTANT: Do NOT apply this file while the live Streamlit deployment still
-- uses an anon/publishable SUPABASE_KEY. The V3 Streamlit server must first use
-- a backend-only Supabase secret key (sb_secret_...) or legacy service_role key.
-- The existing environment variable name SUPABASE_KEY may hold that server-only
-- key; never expose it in browser/client code.
--
-- After the backend key has been changed and the finalize deployment has passed
-- a read + insert + update + delete smoke test, this migration closes the Data
-- API surface to anon/authenticated roles. service_role keeps the existing app
-- access and bypasses RLS by design.

begin;

alter table public.transactions enable row level security;
alter table public.categories enable row level security;

-- This is a private single-user wallet. There is no Supabase Auth ownership
-- column on these legacy tables, so creating a permissive anon/authenticated RLS
-- policy would only make the linter green without adding real protection.
revoke all on table public.transactions from anon, authenticated;
revoke all on table public.categories from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

-- Ledger revision is internal synchronization metadata; the server-side app can
-- read it through service_role after the cutover.
drop policy if exists wy_wallet_ledger_state_read on public.wy_wallet_ledger_state;
revoke all on table public.wy_wallet_ledger_state from anon, authenticated;

-- Public RPC names remain for PostgREST routing, but only the trusted server role
-- may invoke the wallet API after cutover.
revoke execute on function public.wy_wallet_snapshot(integer) from public, anon, authenticated;
revoke execute on function public.wy_wallet_backup_snapshot() from public, anon, authenticated;
revoke execute on function public.wy_wallet_get_ledger_revision() from public, anon, authenticated;
revoke execute on function public.wy_wallet_insert_transaction(date, text, text, text, double precision, text, text, text, uuid) from public, anon, authenticated;
revoke execute on function public.wy_wallet_update_transaction(bigint, timestamp with time zone, date, text, text, text, double precision, text, text, text) from public, anon, authenticated;
revoke execute on function public.wy_wallet_delete_transaction(bigint, timestamp with time zone) from public, anon, authenticated;
revoke execute on function public.wy_wallet_merge_category(text, text) from public, anon, authenticated;

-- Make the intended trusted-server privileges explicit instead of relying only
-- on historical Supabase defaults.
grant select, insert, update, delete on table public.transactions to service_role;
grant select, insert, update, delete on table public.categories to service_role;
grant select on table public.wy_wallet_ledger_state to service_role;
grant usage, select on all sequences in schema public to service_role;
grant execute on function public.wy_wallet_snapshot(integer) to service_role;
grant execute on function public.wy_wallet_backup_snapshot() to service_role;
grant execute on function public.wy_wallet_get_ledger_revision() to service_role;
grant execute on function public.wy_wallet_insert_transaction(date, text, text, text, double precision, text, text, text, uuid) to service_role;
grant execute on function public.wy_wallet_update_transaction(bigint, timestamp with time zone, date, text, text, text, double precision, text, text, text) to service_role;
grant execute on function public.wy_wallet_delete_transaction(bigint, timestamp with time zone) to service_role;
grant execute on function public.wy_wallet_merge_category(text, text) to service_role;

commit;
