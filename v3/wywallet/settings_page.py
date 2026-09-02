from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st

from . import db
from .access import touch_access
from .backup import database_revision, full_backup_snapshot
from .config import APP_VERSION, BACKUP_BUNDLE_TTL_SECONDS, EXPENSE, INCOME, REFUND, TIMEZONE_NAME, TRANSACTION_TYPES, TYPE_LABELS, now_my, today_my
from .exporting import build_backup_excel, safe_csv_bytes
from .ledger_codec import decode_legacy_note, detach_receipt_if_identity_changed, logical_type, physical_payload, receipt_identity_changed
from .ui import page_header, section_title
from .ux import ranked_categories


def _rpc_object(data: Any) -> dict:
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        if len(first) == 1 and isinstance(next(iter(first.values())), dict):
            return next(iter(first.values()))
        return first
    return {}


def _category_usage(transactions: pd.DataFrame, registered: set[str]) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(columns=["类别", "使用笔数", "毛支出", "退款", "净支出", "状态"])
    work = transactions.copy()
    work["毛支出"] = work["amount"].where(work["type"] == EXPENSE, 0.0)
    work["退款"] = work["amount"].where(work["type"] == REFUND, 0.0)
    work["净支出"] = work["毛支出"] - work["退款"]
    usage = (
        work.groupby("category")
        .agg(使用笔数=("amount", "size"), 毛支出=("毛支出", "sum"), 退款=("退款", "sum"), 净支出=("净支出", "sum"))
        .reset_index()
        .rename(columns={"category": "类别"})
        .sort_values(["使用笔数", "净支出"], ascending=[False, False])
    )
    usage["状态"] = usage["类别"].map(lambda value: "已登记" if str(value).casefold() in registered else "历史记录未登记")
    return usage


def _category_section(transactions: pd.DataFrame, categories: list[str]) -> None:
    registered_rows = db.load_category_rows()
    registered = {value.casefold() for value in registered_rows}
    usage = _category_usage(transactions, registered)
    st.dataframe(usage, hide_index=True, width="stretch")

    history_categories = sorted({str(value).strip() for value in transactions.get("category", pd.Series(dtype=str)).dropna() if str(value).strip()})
    missing = [value for value in history_categories if value.casefold() not in registered]
    if missing:
        st.warning("发现未登记历史类别：" + "、".join(missing))
        if st.button("登记全部未登记类别", width="stretch", key="release_register_missing"):
            failures = []
            for name in missing:
                try:
                    db.create_category(name)
                except Exception as exc:
                    failures.append(f"{name}: {exc}")
            if failures:
                st.error("部分登记失败：" + "；".join(failures))
            else:
                st.toast("已登记全部历史类别")
                st.rerun()

    left, right = st.columns(2, gap="large")
    with left:
        section_title("新增类别")
        name = st.text_input("类别名称", max_chars=80, key="release_new_category")
        if st.button("新增类别", type="primary", width="stretch", key="release_create_category"):
            try:
                st.toast("类别已建立" if db.create_category(name) else "类别已存在")
                st.rerun()
            except Exception as exc:
                st.error(f"新增失败：{exc}")

    with right:
        section_title("改名或合并类别")
        ordered = ranked_categories(categories, transactions)
        if not ordered:
            st.info("暂无类别。")
            return
        source = st.selectbox("原类别", ordered, key="release_merge_source")
        mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True, key="release_merge_mode")
        choices = [value for value in ordered if value.casefold() != source.casefold()]
        target = (
            st.selectbox("目标类别", choices, key="release_merge_target")
            if mode == "现有类别" and choices
            else st.text_input("新类别名称", max_chars=80, key="release_merge_new")
        )
        target_text = str(target or "").strip()
        same_target = bool(target_text) and target_text.casefold() == source.casefold()
        if same_target:
            st.warning("目标类别不能与原类别相同。")
        confirmed = st.checkbox("我确认执行类别合并", key="release_merge_confirm")
        disabled = not confirmed or not target_text or same_target
        if st.button("执行改名／合并", disabled=disabled, width="stretch", key="release_merge_submit"):
            try:
                response = db.get_client().rpc("wy_wallet_merge_category", {"p_source": source, "p_target": target_text}).execute()
                result = _rpc_object(response.data)
                db.invalidate_data()
                st.success(f"完成：移动 {int(result.get('moved_rows') or 0)} 笔交易。数据库已在同一个 transaction 内完成移动与类别清理。")
                st.rerun()
            except Exception as exc:
                st.error(f"合并失败：{exc}")


@st.dialog("修复无效交易", width="large")
def _repair_invalid_dialog(raw_row: dict) -> None:
    touch_access()
    try:
        tx_id = int(raw_row.get("id"))
    except Exception:
        st.error("记录 ID 无效，无法通过网页安全更新。")
        return
    expected_updated_at = str(raw_row.get("updated_at") or "")
    if not expected_updated_at:
        st.error("这笔无效记录缺少并发版本信息，请先刷新数据后再修复。")
        return

    parsed_date = pd.to_datetime(raw_row.get("date"), errors="coerce")
    default_date = today_my() if pd.isna(parsed_date) or parsed_date.date() > today_my() else parsed_date.date()
    parsed_amount = pd.to_numeric(raw_row.get("amount"), errors="coerce")
    default_amount = abs(float(parsed_amount)) if not pd.isna(parsed_amount) and float(parsed_amount) != 0 else 0.01
    raw_type = str(raw_row.get("type") or "")
    clean_note, legacy_refund, legacy_receipt_id = decode_legacy_note(raw_row.get("note"))
    structured_type = logical_type(raw_type, str(raw_row.get("flow_subtype") or ""))
    negative_expense = raw_type == EXPENSE and not pd.isna(parsed_amount) and float(parsed_amount) < 0
    if (raw_type == INCOME and legacy_refund) or negative_expense:
        default_type = REFUND
    else:
        default_type = structured_type if structured_type in TRANSACTION_TYPES else EXPENSE
    receipt_id = str(raw_row.get("receipt_id") or legacy_receipt_id or "")
    existing_subtype = str(raw_row.get("flow_subtype") or "").strip() or None

    original_identity = dict(raw_row)
    original_identity["receipt_id"] = receipt_id
    original_identity["type"] = default_type
    if not pd.isna(parsed_amount):
        original_identity["amount"] = abs(float(parsed_amount)) if negative_expense or legacy_refund else float(parsed_amount)
    if not pd.isna(parsed_date):
        original_identity["date"] = parsed_date.date()

    st.caption(f"当前问题：{raw_row.get('issues', '')}")
    c1, c2 = st.columns(2)
    tx_date = c1.date_input("日期", value=default_date, max_value=today_my(), key=f"release_repair_date_{tx_id}")
    tx_type = c2.selectbox("类型", TRANSACTION_TYPES, index=TRANSACTION_TYPES.index(default_type), format_func=lambda value: TYPE_LABELS[value], key=f"release_repair_type_{tx_id}")
    item = st.text_input("项目／商家", value=str(raw_row.get("item") or ""), max_chars=180, key=f"release_repair_item_{tx_id}")
    category = st.text_input("类别", value=str(raw_row.get("category") or ""), max_chars=80, key=f"release_repair_cat_{tx_id}")
    amount = st.number_input("金额", min_value=0.01, step=0.01, value=default_amount, key=f"release_repair_amount_{tx_id}")
    note = st.text_area("备注", value=clean_note, max_chars=1000, key=f"release_repair_note_{tx_id}")

    receipt_linked = bool(receipt_id.strip())
    identity_changed = receipt_linked and receipt_identity_changed(
        original_identity,
        {"date": tx_date, "item": item, "type": tx_type, "amount": amount},
    )
    if identity_changed:
        st.warning("这笔无效记录来自收据；修复后日期、项目、类型或金额会改变，因此保存时会解除旧 Receipt ID，避免把新内容继续挂在旧收据身份上。")
    elif receipt_linked:
        st.caption("这笔记录来自收据。若只修复类别或备注，会保留原 Receipt ID。")

    if not st.button("保存修复", type="primary", width="stretch", key=f"release_repair_submit_{tx_id}"):
        return
    try:
        logical = db.normalize_transaction({
            "date": tx_date,
            "item": item,
            "category": category,
            "type": tx_type,
            "amount": amount,
            "note": note,
            "receipt_id": receipt_id,
        })
        logical, subtype, detached = detach_receipt_if_identity_changed(
            original_identity,
            logical,
            existing_subtype,
        )
        payload = physical_payload(logical, subtype)
        db.get_client().rpc("wy_wallet_update_transaction", {
            "p_id": tx_id,
            "p_expected_updated_at": expected_updated_at,
            "p_date": payload["date"],
            "p_item": payload["item"],
            "p_category": payload["category"],
            "p_type": payload["type"],
            "p_amount": payload["amount"],
            "p_note": payload["note"],
            "p_receipt_id": payload.get("receipt_id"),
            "p_flow_subtype": payload.get("flow_subtype"),
        }).execute()
        db.invalidate_data()
        st.toast("无效交易已修复；原收据关联已解除" if detached else "无效交易已修复并重新纳入报表")
        st.rerun()
    except Exception as exc:
        text = str(exc)
        if "WY_WALLET_CONFLICT" in text or "40001" in text:
            st.error("这笔无效记录已在其他页面被修改。为避免覆盖最新数据，请刷新后重新修复。")
        elif "WY_WALLET_NOT_FOUND" in text or "P0002" in text:
            st.error("这笔记录已经不存在，请刷新页面。")
        else:
            st.error(f"修复失败：{exc}")


def _repair_section(invalid_rows: pd.DataFrame) -> None:
    if invalid_rows.empty:
        st.success("没有发现无效记录。")
        return
    st.warning(f"发现 {len(invalid_rows)} 笔无效记录；它们不会进入报表。")
    st.dataframe(invalid_rows, hide_index=True, width="stretch", height=360)
    row_map: dict[int, dict] = {}
    for _, row in invalid_rows.iterrows():
        try:
            row_map[int(row["id"])] = row.to_dict()
        except Exception:
            continue
    if row_map:
        selected = st.selectbox("选择无效记录进行修复", list(row_map), format_func=lambda value: f"ID {value} · {row_map[value].get('item', '')} · {row_map[value].get('issues', '')}", key="release_invalid_select")
        if st.button("打开修复表单", type="primary", width="stretch", key="release_invalid_open"):
            _repair_invalid_dialog(row_map[selected])
    st.download_button("下载无效记录 CSV", safe_csv_bytes(invalid_rows), f"WY_Wallet_V3_invalid_{today_my()}.csv", mime="text/csv", width="stretch")


def _backup_section() -> None:
    bundle = st.session_state.get("backup_bundle")
    if bundle:
        expired = time.time() - float(bundle.get("created_ts", 0) or 0) > BACKUP_BUNDLE_TTL_SECONDS
        try:
            current_revision, _ = database_revision()
            changed = int(bundle.get("database_revision", -1)) != current_revision
        except Exception:
            changed = True
        if expired or changed:
            st.session_state.pop("backup_bundle", None)
            bundle = None
            st.warning("之前准备的备份已过期或数据库已更新，请重新准备。")

    st.caption("完整备份由 PostgreSQL 单条 SQL/MVCC 快照生成；不会出现分页过程中前半旧、后半新的混合时间点。")
    if st.button("准备最新完整备份", type="primary", width="stretch", key="release_prepare_backup"):
        try:
            with st.spinner("正在生成数据库一致性快照..."):
                snap = full_backup_snapshot()
                export = snap["transactions"].copy()
                if not export.empty:
                    export["date"] = export["date"].dt.date
                category_df = pd.DataFrame({"name": snap["categories"]})
                invalid = snap["invalid"]
                metadata = pd.DataFrame([
                    ["export_time", now_my().isoformat()],
                    ["timezone", TIMEZONE_NAME],
                    ["currency", "MYR"],
                    ["valid_transaction_count", len(export)],
                    ["invalid_transaction_count", len(invalid)],
                    ["registered_or_used_category_count", len(category_df)],
                    ["app_version", APP_VERSION],
                    ["database_revision", snap["database_revision"]],
                    ["database_revision_updated_at", snap["database_revision_updated_at"]],
                ], columns=["key", "value"])
                st.session_state["backup_bundle"] = {
                    "excel": build_backup_excel(export, category_df, metadata, invalid),
                    "csv": safe_csv_bytes(export),
                    "time": now_my().isoformat(timespec="seconds"),
                    "created_ts": time.time(),
                    "database_revision": int(snap["database_revision"]),
                }
        except Exception as exc:
            st.error(f"准备备份失败：{exc}")

    bundle = st.session_state.get("backup_bundle")
    if bundle:
        st.success(f"备份已准备：{bundle['time']}")
        d1, d2 = st.columns(2)
        d1.download_button("下载最新完整 Excel 备份", bundle["excel"], f"WY_Wallet_V3_{today_my()}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
        d2.download_button("下载最新交易 CSV", bundle["csv"], f"WY_Wallet_V3_{today_my()}.csv", mime="text/csv", width="stretch")
        st.caption("备份保留 receipt_id、flow_subtype、updated_at 等恢复/审计元数据；下载前用数据库 revision 检查跨设备 freshness。")


def render(transactions: pd.DataFrame, invalid_rows: pd.DataFrame, categories: list[str]) -> None:
    touch_access()
    page_header("设置与备份", "类别合并使用数据库原子事务；完整备份使用 PostgreSQL 一致性快照。")
    section = st.segmented_control("设置区块", ["类别管理", "数据修复", "备份"], default="类别管理", key="release_settings_section")
    if section == "类别管理":
        _category_section(transactions, categories)
    elif section == "数据修复":
        _repair_section(invalid_rows)
    else:
        _backup_section()
