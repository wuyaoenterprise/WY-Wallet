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
    assert "physical_payload" in source


def test_transactions_page_has_safe_paging_refresh_equal_metrics_and_truncation_notice():
    source = _source("v3/wywallet/transactions_page.py")
    assert 'page_slice("分页", "oc_table_page"' in source
    assert 'page_slice("卡片分页", "oc_card_page"' in source
    assert "clear_snapshot_cache()" in source
    assert "a, b, c, d, e = st.columns(5" in source
    assert "ranked_categories" in source
    assert "truncated: bool = False" in source
    assert "搜索、筛选和分页仅针对这部分数据" in source
    assert "st.dataframe(display" in source


def test_manual_entry_warns_on_exact_duplicates_without_blocking():
    source = _source("v3/wywallet/transaction_commands.py")
    assert "exact_duplicate_count" in source
    assert "完全相同的交易" in source
    assert "仍可保存" in source
    assert "physical_payload" in source
    assert "min_value=0.01" in source
    assert "max_chars=180" in source
    assert "max_chars=1000" in source


def test_undo_uses_idempotent_restore_instead_of_ambiguous_duplicate_block():
    source = _source("v3/wywallet/transactions_page.py")
    assert '"undo_token": str(uuid.uuid4())' in source
    assert '"p_client_token": token' in source
    assert "wy_wallet_insert_transaction" in source
    assert "数据库已经存在同等交易，因此没有重复恢复" not in source


def test_settings_page_uses_atomic_merge_mvcc_backup_and_hardened_repair():
    source = _source("v3/wywallet/settings_page.py")
    assert "wy_wallet_merge_category" in source
    assert "full_backup_snapshot" in source
    assert "database_revision" in source
    assert "wy_wallet_update_transaction" in source
    assert "p_expected_updated_at" in source
    assert "from . import db" in source
    assert "web." not in source
    assert "disabled=disabled" in source
    assert '"build_id"' not in source
    assert "structured_type = logical_type(raw_type" in source
    assert "decode_legacy_note" in source
    assert "legacy_refund" in source


def test_receipt_page_recomputes_identity_after_human_edits_and_scopes_confirmations():
    source = _source("v3/pages/receipt.py")
    assert "receipt_draft_" in source
    assert "_store_draft" in source
    assert "_clear_target_editor_state" in source
    assert "_clear_receipt_session_state" in source
    assert "flow_subtype" in source
    assert "physical_payload" in source
    assert '.table("transactions").insert(payloads)' in source
    assert "insert_transactions" not in source
    assert "_date_future" in source
    assert "AI 识别到未来日期" in source
    assert "a, b, c, d, e = st.columns(5" in source
    assert "BUILD_ID" not in source
    assert "st.caption(APP_VERSION)" in source
    assert "identity_rows = edited.to_dict" in source
    assert "root_id = receipt_root_id(payload, identity_rows)" in source
    assert 'key=f"force_whole_receipt_{image_signature}"' in source
    assert 'key=f"receipt_difference_confirm_{image_signature}"' in source
    assert 'key=f"receipt_final_confirm_{image_signature}"' in source
    assert 'edited["仍然保存重复"] = edited["保存"]' in source
    assert "receipt_presence" in source
    assert "partial_saved" in source
    assert "_clear_receipt_session_state(previous_signature)" in source
    assert source.count("_clear_receipt_session_state(image_signature)") >= 4


def test_main_app_routes_to_dedicated_hardened_pages():
    source = _source("v3/app.py")
    assert "transactions_page.render" in source
    assert "ai_page.render" in source
    assert "settings_page.render" in source
    assert "dashboard_page.render" in source
    assert "reports_page.render" in source
    assert "web._dashboard" not in source
    assert "web._reports_page" not in source
    assert "total_count=total_count" in source


def test_reports_page_avoids_misleading_partial_visuals():
    source = _source("v3/wywallet/reports_page.py")
    assert "_pie_with_other" in source
    assert '"其余类别"' in source
    assert '.nlargest(15, "_impact")' in source
    assert "不绘制虚假的 0 元同比曲线" in source
    assert "m1, m2, m3, m4, m5 = st.columns(5" in source
    assert "a, b, c, d, e = st.columns(5" in source
    assert "historical_monthly_average" in source
    assert "first_complete_tracking_month" in source
    assert "本年度没有正净支出月份" in source
    assert "recurring_items_by_category" in source
    assert "invalid_quality_for_year" in source
    assert "无效日期无法归年" in source


def test_new_transaction_uses_revision_guarded_fast_snapshot_patch():
    commands = _source("v3/wywallet/transaction_commands.py")
    snapshot = _source("v3/wywallet/snapshot.py")
    assert "patch_session_snapshot_after_insert" in commands
    assert "expected_revision_delta=1 + int(category_created)" in commands
    assert "wy_wallet_get_ledger_revision" in snapshot
    assert "revision[\"revision\"] != old_revision" in snapshot
    assert "database_revision" in snapshot
    assert "logical_type" in snapshot


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
    assert "st.dataframe(display" in source


def test_metric_cards_and_muted_text_are_theme_aware():
    source = _source("v3/wywallet/ui.py")
    assert "min-height:118px" in source
    assert "min-height:104px" in source
    assert "color-mix" in source
    assert "var(--text-color" in source
    assert "flex-wrap:wrap!important" in source
    assert "overflow:visible" in source


def test_ai_chat_renders_user_message_immediately_and_clamps_lists():
    source = _source("v3/wywallet/ai_page.py")
    assert 'with st.chat_message("user")' in source
    assert 'page_slice("分页", "ai_release_page"' in source


def test_displayed_version_is_compact_semver_only():
    config = _source("v3/wywallet/config.py")
    app = _source("v3/app.py")
    receipt = _source("v3/pages/receipt.py")
    assert 'APP_VERSION = "v3.2.5"' in config
    assert "BUILD_ID" not in app
    assert "st.caption(APP_VERSION)" in app
    assert "BUILD_ID" not in receipt
    assert "st.caption(APP_VERSION)" in receipt
