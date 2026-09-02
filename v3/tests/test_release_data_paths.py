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


def test_transactions_page_has_complete_card_paging_and_forced_refresh():
    source = _source("v3/wywallet/transactions_page.py")
    assert 'key="oc_card_page"' in source
    assert "filtered.iloc[start:start + page_size]" in source
    assert "clear_snapshot_cache()" in source
    assert "a, b, c, d, e = st.columns(5" in source


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
    assert "dashboard_page.render" in source
    assert "web._dashboard" not in source


def test_new_transaction_uses_revision_guarded_fast_snapshot_patch():
    commands = _source("v3/wywallet/transaction_commands.py")
    snapshot = _source("v3/wywallet/snapshot.py")
    assert "patch_session_snapshot_after_insert" in commands
    assert "expected_revision_delta=1 + int(category_created)" in commands
    assert "wy_wallet_get_ledger_revision" in snapshot
    assert "revision[\"revision\"] != old_revision" in snapshot
    assert "database_revision" in snapshot


def test_main_app_uses_one_snapshot_without_nested_fragments():
    source = _source("v3/app.py")
    assert source.count("current_snapshot()") == 1
    assert "@st.fragment" not in source


def test_password_gate_supports_enter_submit():
    source = _source("v3/wywallet/access.py")
    assert "st.form(" in source
    assert "enter_to_submit=True" in source
    assert "st.form_submit_button" in source


def test_dashboard_month_window_is_calendar_anchored_and_equal_width():
    source = _source("v3/wywallet/dashboard_page.py")
    assert "pd.period_range(end=current_period" in source
    assert "groupby([\"year\", \"month\"]" in source
    assert "m1, m2, m3, m4, m5 = st.columns(5" in source
    assert "category_orders" in source


def test_displayed_version_is_compact_semver_only():
    config = _source("v3/wywallet/config.py")
    app = _source("v3/app.py")
    assert 'APP_VERSION = "v3.2.2"' in config
    assert "BUILD_ID" not in app
    assert "st.caption(APP_VERSION)" in app
