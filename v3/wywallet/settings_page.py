from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st

from . import db, web
from .access import touch_access
from .backup import database_revision, full_backup_snapshot
from .config import APP_VERSION, BACKUP_BUNDLE_TTL_SECONDS, BUILD_ID, EXPENSE, REFUND, TIMEZONE_NAME, now_my, today_my
from .exporting import build_backup_excel, safe_csv_bytes
from .ui import page_header, section_title


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
        name = st.text_input("类别名称", key="release_new_category")
        if st.button("新增类别", type="primary", width="stretch", key="release_create_category"):
            try:
                st.toast("类别已建立" if db.create_category(name) else "类别已存在")
                st.rerun()
            except Exception as exc:
                st.error(f"新增失败：{exc}")

    with right:
        section_title("改名或合并类别")
        if not categories:
            st.info("暂无类别。")
            return
        source = st.selectbox("原类别", categories, key="release_merge_source")
        mode = st.radio("目标", ["现有类别", "新名称"], horizontal=True, key="release_merge_mode")
        choices = [value for value in categories if value.casefold() != source.casefold()]
        target = (
            st.selectbox("目标类别", choices, key="release_merge_target")
            if mode == "现有类别" and choices
            else st.text_input("新类别名称", key="release_merge_new")
        )
        confirmed = st.checkbox("我确认执行类别合并", key="release_merge_confirm")
        if st.button("执行改名／合并", disabled=not confirmed, width="stretch", key="release_merge_submit"):
            try:
                response = db.get_client().rpc("wy_wallet_merge_category", {"p_source": source, "p_target": target}).execute()
                result = _rpc_object(response.data)
                db.invalidate_data()
                st.success(f"完成：移动 {int(result.get('moved_rows') or 0)} 笔交易。数据库已在同一个 transaction 内完成移动与类别清理。")
                st.rerun()
            except Exception as exc:
                st.error(f"合并失败：{exc}")


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
            web.repair_invalid_dialog(row_map[selected])
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
                invalid_rows = snap["invalid"]
                metadata = pd.DataFrame([
                    ["export_time", now_my().isoformat()],
                    ["timezone", TIMEZONE_NAME],
                    ["currency", "MYR"],
                    ["valid_transaction_count", len(export)],
                    ["invalid_transaction_count", len(invalid_rows)],
                    ["registered_or_used_category_count", len(category_df)],
                    ["app_version", APP_VERSION],
                    ["build_id", BUILD_ID],
                    ["database_revision", snap["database_revision"]],
                    ["database_revision_updated_at", snap["database_revision_updated_at"]],
                ], columns=["key", "value"])
                st.session_state["backup_bundle"] = {
                    "excel": build_backup_excel(export, category_df, metadata, invalid_rows),
                    "csv": safe_csv_bytes(export),
                    "time": now_my().isoformat(timespec="seconds"),
                    "created_ts": time.time(),
                    "database_revision": int(snap["database_revision"]),
                }
                bundle = st.session_state["backup_bundle"]
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
