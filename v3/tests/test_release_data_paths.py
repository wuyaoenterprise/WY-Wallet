from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_transactions_page_uses_database_optimistic_concurrency():
    source = _source("v3/wywallet/transactions_page.py")
    assert "wy_wallet_update_transaction" in source
    assert "wy_wallet_delete_transaction" in source
    assert "p_expected_updated_at" in source
    assert "WY_WALLET_CONFLICT" in source


def test_settings_page_uses_atomic_merge_and_mvcc_backup():
    source = _source("v3/wywallet/settings_page.py")
    assert "wy_wallet_merge_category" in source
    assert "full_backup_snapshot" in source
    assert "database_revision" in source
    assert "fetch_stable_backup_snapshot" not in source


def test_receipt_page_never_uses_multi_page_fresh_ledger_loader():
    source = _source("v3/pages/receipt.py")
    assert "current_snapshot" in source
    assert "fresh_snapshot" in source
    assert "fetch_transactions_interactive_fresh" not in source
    assert "load_transactions" not in source


def test_main_app_routes_to_dedicated_hardened_pages():
    source = _source("v3/app.py")
    assert "transactions_page.render" in source
    assert "ai_page.render" in source
    assert "settings_page.render" in source


def test_new_transaction_uses_revision_guarded_fast_snapshot_patch():
    commands = _source("v3/wywallet/transaction_commands.py")
    snapshot = _source("v3/wywallet/snapshot.py")
    assert "patch_session_snapshot_after_insert" in commands
    assert "expected_revision_delta=1 + int(category_created)" in commands
    assert "wy_wallet_get_ledger_revision" in snapshot
    assert "revision[\"revision\"] != old_revision" in snapshot
    assert "database_revision" in snapshot


def test_main_app_does_not_refetch_snapshot_inside_fragments():
    source = _source("v3/app.py")
    assert source.count("current_snapshot()") == 1
