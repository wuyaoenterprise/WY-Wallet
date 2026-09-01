from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import re
import time
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from google.genai import types

import wywallet.ai as ai
import wywallet.analytics as analytics
import wywallet.db as db
import wywallet.receipt as receipt
import wywallet.web as web
from wywallet.config import EXPENSE, INCOME, REFUND, TRANSACTION_TYPES, TYPE_LABELS, today_my

SESSION_TTL_SECONDS = 30 * 60
_EXPLANATION_TOKENS = ("为什么", "為什麼", "原因", "解释", "解釋", "怎么", "怎麼", "趋势", "趨勢")
_ORIGINALS: dict[str, object] = {}
_APPLIED = False


def _truthy_secret(name: str) -> bool:
    try:
        return str(st.secrets.get(name, "") or "").strip().casefold() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def _require_private_access_v3() -> str:
    try:
        configured = str(st.secrets.get("WEB_ACCESS_PASSWORD", "") or "")
    except Exception:
        configured = ""
    if not configured:
        if _truthy_secret("ALLOW_UNPROTECTED_ACCESS"):
            return "platform-private"
        st.set_page_config(page_title="WY Wallet V3", page_icon="💳", layout="centered")
        web.inject_css()
        web.page_header(
            "安全设置未完成",
            "请在 Streamlit Secrets 配置 WEB_ACCESS_PASSWORD；若 App 已由平台设为 Private，才可明确设置 ALLOW_UNPROTECTED_ACCESS = true。",
        )
        st.stop()

    now = time.time()
    if st.session_state.get("web_access_ok"):
        authenticated_at = float(st.session_state.get("web_access_ok_at", now) or now)
        if now - authenticated_at <= SESSION_TTL_SECONDS:
            st.session_state["web_access_ok_at"] = now
            return "password"
        st.session_state.pop("web_access_ok", None)
        st.session_state.pop("web_access_ok_at", None)
        st.info("访问会话已过期，请重新输入密码。")

    lock_until = float(st.session_state.get("web_access_lock_until", 0) or 0)
    remaining = int(max(lock_until - now, 0))
    st.set_page_config(page_title="WY Wallet V3", page_icon="💳", layout="centered")
    web.inject_css()
    web.page_header("WY Wallet 私人访问", "请输入部署环境中的 WEB_ACCESS_PASSWORD。登录会话闲置 30 分钟后自动失效。")
    if remaining > 0:
        st.error(f"连续输错次数过多，请 {remaining} 秒后再试。")
        st.stop()
    entered = st.text_input("访问密码", type="password", key="v3_access_password")
    if st.button("进入", type="primary", width="stretch", key="v3_access_submit"):
        if hmac.compare_digest(entered, configured):
            st.session_state["web_access_ok"] = True
            st.session_state["web_access_ok_at"] = now
            st.session_state.pop("web_access_fail_count", None)
            st.session_state.pop("web_access_lock_until", None)
            st.rerun()
        count = int(st.session_state.get("web_access_fail_count", 0)) + 1
        if count >= 5:
            st.session_state["web_access_fail_count"] = 0
            st.session_state["web_access_lock_until"] = now + 30
            st.error("密码连续错误 5 次，已暂时锁定 30 秒。")
        else:
            st.session_state["web_access_fail_count"] = count
            st.error(f"密码不正确。还可尝试 {5 - count} 次后进入短暂冷却。")
    st.stop()
    return "password"


def expire_access_session_if_needed() -> None:
    if not st.session_state.get("web_access_ok"):
        return
    now = time.time()
    authenticated_at = float(st.session_state.get("web_access_ok_at", now) or now)
    if now - authenticated_at > SESSION_TTL_SECONDS:
        st.session_state.pop("web_access_ok", None)
        st.session_state.pop("web_access_ok_at", None)
    else:
        st.session_state["web_access_ok_at"] = now


def render_session_controls() -> None:
    if not st.session_state.get("web_access_ok"):
        return
    with st.sidebar:
        if st.button("🔒 锁定此会话", width="stretch", key="v3_lock_session"):
            st.session_state.pop("web_access_ok", None)
            st.session_state.pop("web_access_ok_at", None)
            st.rerun()


def _aggregation_v3(frame: pd.DataFrame, aggregation: str, flow: str, start: date | None = None, end: date | None = None) -> float:
    original = _ORIGINALS["aggregate"]
    if aggregation != "average_month" or not start or not end:
        return original(frame, aggregation, flow, start, end)  # type: ignore[misc]

    today = ai.today_my()
    effective_frame = frame
    effective_end = end
    current_month_incomplete = end >= today and today.day < calendar.monthrange(today.year, today.month)[1]
    completed_end = date(today.year, today.month, 1) - timedelta(days=1)
    if current_month_incomplete and start <= completed_end:
        effective_end = min(end, completed_end)
        effective_frame = ai._filter_range(frame, start, effective_end)
    elif current_month_incomplete and start > completed_end:
        effective_end = min(end, today)

    total = ai._amount_total(effective_frame, flow)
    months = (effective_end.year - start.year) * 12 + effective_end.month - start.month + 1
    return round(total / max(months, 1), 2)


def _planner_v3(question: str, selected_year: int, transactions: pd.DataFrame,
                conversation_state: dict | None, recent_history: list[dict] | None) -> ai.FinanceQueryPlan:
    state = conversation_state or {}
    # Privacy-first planning: do not send thousands of merchant names to Gemini.
    # The model identifies the user's subject text; exact ledger matching happens locally.
    prompt = {
        "current_malaysia_date": ai.today_my().isoformat(),
        "ui_selected_year": int(selected_year),
        "previous_state": state,
        "recent_dialogue": (recent_history or [])[-8:],
        "current_question": question,
    }
    response = ai._generate_content_with_retry(
        model=ai.GEMINI_MODEL,
        contents=json.dumps(prompt, ensure_ascii=False, default=str),
        config=types.GenerateContentConfig(
            system_instruction=ai.SYSTEM_LEDGER_PARSER,
            response_mime_type="application/json",
            response_schema=ai.FinanceQueryPlan,
        ),
    )
    plan = response.parsed if isinstance(response.parsed, ai.FinanceQueryPlan) else ai.FinanceQueryPlan.model_validate_json(response.text)

    if plan.subject_mode == "inherit":
        if state:
            plan.subject = state.get("subject")
            plan.matched_items = list(state.get("matched_items") or [])
            plan.matched_categories = list(state.get("matched_categories") or [])
        else:
            plan.subject_mode = "all"
    elif plan.subject_mode == "all":
        plan.subject = None
        plan.matched_items = []
        plan.matched_categories = []

    plan.aggregation = (state.get("aggregation") if plan.aggregation_mode == "inherit" else plan.aggregation) or "amount"
    if plan.aggregation == "average":
        plan.aggregation = "average_transaction"
    plan.flow = (state.get("flow") if plan.flow_mode == "inherit" else plan.flow) or "expense"
    if plan.flow == "net" and plan.aggregation not in {"amount", "average_day", "average_month"}:
        plan.aggregation = "amount"

    if plan.subject_mode == "specific" and plan.subject:
        local_items, local_categories = ai._fallback_subject_matches(plan.subject, transactions)
        plan.matched_items = list(dict.fromkeys(list(plan.matched_items) + local_items))
        plan.matched_categories = list(dict.fromkeys(list(plan.matched_categories) + local_categories))
    else:
        item_set = set(ai._candidate_values(transactions, "item", limit=100_000))
        category_set = set(ai._candidate_values(transactions, "category", limit=100_000))
        plan.matched_items = [value for value in plan.matched_items if value in item_set]
        plan.matched_categories = [value for value in plan.matched_categories if value in category_set]

    ai._normalize_comparison_language(question, plan)
    compact = re.sub(r"\s+", "", str(question or "").casefold())
    followup_compare = any(token in compact for token in ["差多少", "差幾多", "百分比", "几%", "幾%", "变化", "變化", "多了", "少了"])
    if plan.comparison == "none" and followup_compare and state.get("comparison") not in {None, "none"}:
        plan.comparison = state.get("comparison")
        plan.comparison_date_from = state.get("comparison_date_from")
        plan.comparison_date_to = state.get("comparison_date_to")

    if ai._comparison_followup_uses_prior_primary_range(question, plan, state):
        plan.time_mode = "inherit"
        plan.date_from = None
        plan.date_to = None
    start, end = ai._resolve_time(plan, selected_year, state)
    plan.date_from, plan.date_to, plan.time_mode = start.isoformat(), end.isoformat(), "specific"
    if plan.comparison == "custom":
        cs, ce = ai._parse_iso(plan.comparison_date_from), ai._parse_iso(plan.comparison_date_to)
        if cs and not ce:
            ce = cs
        if ce and not cs:
            cs = ce
        if cs and ce and cs > ce:
            cs, ce = ce, cs
        plan.comparison_date_from = cs.isoformat() if cs else None
        plan.comparison_date_to = ce.isoformat() if ce else None
    return plan


def _execute_v3(plan: ai.FinanceQueryPlan, transactions: pd.DataFrame) -> dict:
    original = _ORIGINALS["execute"]
    result = original(plan, transactions)  # type: ignore[misc]
    aggregation = plan.aggregation or "amount"
    if aggregation not in {"max_transaction", "min_transaction"}:
        return result

    start = ai._parse_iso(plan.date_from) or date(ai.today_my().year, 1, 1)
    end = min(ai._parse_iso(plan.date_to) or date(ai.today_my().year, 12, 31), ai.today_my())
    base, _, _ = ai._filter_subject(transactions.copy(), plan)
    ranged = ai._filter_range(base, start, end) if start <= end else base.iloc[0:0].copy()
    rows = ai._relevant_rows(ranged, aggregation, plan.flow or "expense", plan.intent)
    if rows.empty:
        result["extreme_transaction"] = None
        return result
    target = float(rows["amount"].max() if aggregation == "max_transaction" else rows["amount"].min())
    matches = rows[rows["amount"].astype(float) == target].sort_values(["date", "id"], ascending=[False, False])
    row = matches.iloc[0]
    result["extreme_transaction"] = {
        "date": pd.to_datetime(row["date"]).date().isoformat(),
        "item": str(row["item"]),
        "category": str(row["category"]),
        "type": str(row["type"]),
        "amount": round(float(row["amount"]), 2),
    }
    return result


def _format_aggregate(value: float, aggregation: str) -> str:
    return f"{int(value):,} 笔" if aggregation == "count" else f"RM {float(value):,.2f}"


def _summary_v3(result: dict) -> str:
    plan = result.get("plan") or {}
    aggregation, flow = plan.get("aggregation") or "amount", plan.get("flow") or "expense"
    labels = {"expense": "净支出", "income": "收入", "refund": "退款", "net": "结余", "all": "全部交易"}
    agg_labels = {
        "amount": "金额", "count": "笔数", "average": "平均每笔", "average_transaction": "平均每笔",
        "average_day": "每日平均", "average_month": "每月平均", "median": "中位数",
        "max_transaction": "最大一笔", "min_transaction": "最小一笔",
    }
    total = float(result.get("authoritative_total", 0) or 0)
    lines = [
        f"**本地精确结果｜{labels.get(flow, flow)} · {agg_labels.get(aggregation, aggregation)}：{_format_aggregate(total, aggregation)}**",
        f"范围：{result.get('date_from')} ～ {result.get('date_to')}",
    ]
    matched = list(result.get("matched_items") or []) + list(result.get("matched_categories") or [])
    if matched:
        lines.append("匹配范围：" + "、".join(list(dict.fromkeys(str(v) for v in matched))[:20]))
    extreme = result.get("extreme_transaction")
    if extreme:
        lines.append(f"对应交易：{extreme['date']} · {extreme['item']} · {extreme['category']} · RM {float(extreme['amount']):,.2f}")
    comp = result.get("comparison")
    if comp:
        comp_value = _format_aggregate(float(comp["value"]), aggregation)
        delta_value = f"{int(comp['delta']):+,} 笔" if aggregation == "count" else f"RM {float(comp['delta']):+,.2f}"
        pct = "N/A" if comp.get("percent") is None else f"{float(comp['percent']):+.1f}%"
        lines.append(f"对比：{comp['date_from']} ～ {comp['date_to']} = {comp_value}；差额 {delta_value}（{pct}）")
    if plan.get("comparison") == "highest" and result.get("highest_month"):
        row = result["highest_month"]
        lines.append(f"最高月份：{row['label']} · {_format_aggregate(float(row['value']), aggregation)}")
    if plan.get("comparison") == "lowest" and result.get("lowest_month"):
        row = result["lowest_month"]
        lines.append(f"最低月份：{row['label']} · {_format_aggregate(float(row['value']), aggregation)}")
    if plan.get("intent") == "trend":
        monthly = result.get("monthly") or []
        if monthly and len(monthly) <= 24:
            lines.append("月份：" + "；".join(f"{row['label']} {_format_aggregate(float(row['value']), aggregation)}" for row in monthly))
    return "\n\n".join(lines)


def _answer_v3(question: str, result: dict) -> str:
    plan = result.get("plan") or {}
    intent = plan.get("intent")
    if intent not in {"explain", "compare", "trend"} and not any(token in str(question) for token in _EXPLANATION_TOKENS):
        return ""
    compact = ai._compact_result_for_ai(result)
    if plan.get("subject_mode") == "all" and intent not in {"explain", "compare", "trend"}:
        compact.pop("item_summary", None)
    if intent not in {"trend", "compare", "explain"}:
        compact.pop("monthly", None)
    payload = {"question": question, "locally_calculated_context": compact}
    try:
        response = ai._generate_content_with_retry(
            model=ai.GEMINI_MODEL,
            contents=json.dumps(payload, ensure_ascii=False, default=str),
            config=types.GenerateContentConfig(system_instruction=ai.SYSTEM_FINANCE_EXPLANATION),
        )
        return (response.text or "").strip()
    except Exception:
        return ""


def _state_v3(plan: ai.FinanceQueryPlan, result: dict | None = None) -> dict:
    state = _ORIGINALS["state"](plan, result)  # type: ignore[misc]
    state["comparison"] = plan.comparison
    state["comparison_date_from"] = plan.comparison_date_from
    state["comparison_date_to"] = plan.comparison_date_to
    return state


def _semantic_receipt_id(transactions, tax: float, service_charge: float, discount: float) -> str:
    normalized = []
    for row in transactions:
        normalized.append({
            "date": str(row.get("date") or ""),
            "item": re.sub(r"\s+", " ", str(row.get("item") or "").strip().casefold()),
            "type": str(row.get("type") or ""),
            "amount": round(float(pd.to_numeric(row.get("amount"), errors="coerce") or 0), 2),
        })
    normalized.sort(key=lambda r: (r["date"], r["item"], r["type"], r["amount"]))
    seed = json.dumps({"rows": normalized, "tax": round(float(tax), 2), "service": round(float(service_charge), 2), "discount": round(float(discount), 2)}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _materialize_receipt_adjustments_v3(transactions, *, tax=0.0, service_charge=0.0, discount=0.0,
                                         fallback_category="其他", receipt_id=None):
    semantic_id = _semantic_receipt_id(transactions, tax, service_charge, discount)
    return _ORIGINALS["materialize_receipt"](
        transactions,
        tax=tax,
        service_charge=service_charge,
        discount=discount,
        fallback_category=fallback_category,
        receipt_id=semantic_id,
    )


def _finalize_receipt_candidates_v3(candidates, fresh_existing_keys):
    rows, skipped = _ORIGINALS["finalize_receipt"](candidates, fresh_existing_keys)  # type: ignore[misc]
    # Never save a partial receipt after the final fresh duplicate check changed
    # the candidate set. Force a rerun so reconciliation and confirmation match
    # the exact rows that will actually be inserted.
    if skipped:
        db.refresh_data()
        return [], skipped
    return rows, 0


def _existing_transaction_keys_fresh(*, fresh: bool = False):
    return _ORIGINALS["existing_keys"](fresh=True)  # type: ignore[misc]


def _dashboard_v3(transactions: pd.DataFrame) -> None:
    _ORIGINALS["dashboard"](transactions)  # type: ignore[misc]
    current = analytics.month_slice(transactions, web.now_my().year, web.now_my().month)
    negative = analytics.net_expense_by_category(current)
    negative = negative[negative["amount"] < 0].copy()
    if not negative.empty:
        web.section_title("本月净退款类别")
        show = negative.copy()
        show["净退款"] = (-show["amount"]).round(2)
        st.dataframe(show[["category", "净退款"]].rename(columns={"category": "类别"}), hide_index=True, width="stretch")
        st.caption("这些类别退款高于当月支出，因此会抵减总净支出；快速正支出排行不会把它们隐藏。")


def _reports_v3(transactions: pd.DataFrame, invalid_rows: pd.DataFrame) -> None:
    _ORIGINALS["reports"](transactions, invalid_rows)  # type: ignore[misc]
    effects = analytics.net_expense_by_category(transactions)
    negative = effects[effects["amount"] < 0].copy() if not effects.empty else effects
    if not negative.empty:
        with st.expander("净退款类别对账", expanded=False):
            show = negative.copy()
            show["净退款"] = (-show["amount"]).round(2)
            st.dataframe(show[["category", "净退款"]].rename(columns={"category": "类别"}), hide_index=True, width="stretch")
            st.caption("该表显示全账本中净退款为负支出的类别，用于补足只展示正净支出的图表。")


def _settings_v3(transactions: pd.DataFrame, invalid_rows: pd.DataFrame, categories: list[str]) -> None:
    section = st.session_state.get("settings_section")
    bundle = st.session_state.get("backup_bundle")
    if section == "备份" and bundle and bundle.get("ledger_signature"):
        fresh, _, _ = db.fetch_transactions_interactive_fresh()
        if db.ledger_signature(fresh) != bundle.get("ledger_signature"):
            st.session_state.pop("backup_bundle", None)
            st.warning("账本已在其他会话更新，之前准备的备份已失效，请重新准备。")

    adjusted = transactions.copy()
    if not adjusted.empty:
        adjusted.loc[adjusted["type"] == REFUND, "amount"] = -adjusted.loc[adjusted["type"] == REFUND, "amount"].astype(float)
    _ORIGINALS["settings"](adjusted, invalid_rows, categories)  # type: ignore[misc]

    current_bundle = st.session_state.get("backup_bundle")
    if current_bundle is not None and not current_bundle.get("ledger_signature"):
        fresh, _, _ = db.fetch_transactions_interactive_fresh()
        current_bundle["ledger_signature"] = db.ledger_signature(fresh)
        st.session_state["backup_bundle"] = current_bundle
    if st.session_state.get("settings_section") == "类别管理":
        st.caption("类别管理中的累计金额已按退款负向抵扣；收入类仍保持正值。")


def _merge_category_safer(source: str, target: str):
    source = str(source or "").strip()
    target = str(target or "").strip()
    if not source or not target or source.casefold() == target.casefold():
        raise ValueError("请选择不同的原类别和目标类别。")
    client = db.get_client()
    registered_rows = db.load_category_rows()
    registered_map = {value.casefold(): value for value in registered_rows}
    known_map = {value.casefold(): value for value in db.load_categories(db.load_transactions())}
    target = known_map.get(target.casefold(), target)
    target_created = False
    moved_ids: list[int] = []
    try:
        if target.casefold() not in registered_map:
            client.table("categories").insert({"name": target[:80]}).execute()
            target_created = True
        source_ids = db._transaction_ids_for_category(client, source)
        for start in range(0, len(source_ids), db.DB_BATCH_SIZE):
            chunk = source_ids[start:start + db.DB_BATCH_SIZE]
            if chunk:
                client.table("transactions").update({"category": target}).in_("id", chunk).execute()
                moved_ids.extend(chunk)
        if db._transaction_ids_for_category(client, source):
            raise RuntimeError("部分交易仍在原类别，正在尝试安全回滚。")
        cleanup_note = ""
        source_removed = True
        try:
            for exact_name in db._registered_name_variants(client, source):
                client.table("categories").delete().eq("name", exact_name).execute()
        except Exception:
            source_removed = False
            cleanup_note = "交易已移动，但原类别登记删除失败；它可能暂时以空类别保留。"
        return db.MergeResult(len(source_ids), target_created, source_removed, cleanup_note)
    except Exception as exc:
        rollback_error = None
        if moved_ids:
            try:
                # Only roll back rows that are still at the merge target. If a
                # concurrent editor changed one to another category, do not
                # overwrite that newer user action.
                for start in range(0, len(moved_ids), db.DB_BATCH_SIZE):
                    chunk = moved_ids[start:start + db.DB_BATCH_SIZE]
                    if chunk:
                        client.table("transactions").update({"category": source}).in_("id", chunk).eq("category", target).execute()
            except Exception as rollback_exc:
                rollback_error = rollback_exc
        if target_created:
            try:
                db._delete_empty_category_if_safe(client, target)
            except Exception:
                pass
        if rollback_error is not None:
            raise RuntimeError(f"类别合并失败，且安全回滚未完全成功：{rollback_error}。请刷新后检查数据。") from exc
        raise
    finally:
        db.invalidate_data()


def apply_overrides() -> None:
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    _ORIGINALS.update({
        "aggregate": ai._aggregate,
        "execute": ai.execute_finance_plan,
        "state": ai.state_from_plan,
        "materialize_receipt": receipt.materialize_receipt_adjustments,
        "finalize_receipt": receipt.finalize_receipt_candidates,
        "existing_keys": db.existing_transaction_keys,
        "dashboard": web._dashboard,
        "reports": web._reports_page,
        "settings": web._settings_page,
    })

    web._require_private_access = _require_private_access_v3
    ai._aggregate = _aggregation_v3
    ai.plan_finance_question = _planner_v3
    ai.execute_finance_plan = _execute_v3
    ai.authoritative_summary_markdown = _summary_v3
    ai.answer_finance_question = _answer_v3
    ai.state_from_plan = _state_v3
    web.plan_finance_question = _planner_v3
    web.execute_finance_plan = _execute_v3
    web.authoritative_summary_markdown = _summary_v3
    web.answer_finance_question = _answer_v3
    web.state_from_plan = _state_v3

    receipt.materialize_receipt_adjustments = _materialize_receipt_adjustments_v3
    receipt.finalize_receipt_candidates = _finalize_receipt_candidates_v3
    db.existing_transaction_keys = _existing_transaction_keys_fresh

    web._dashboard = _dashboard_v3
    web._reports_page = _reports_v3
    web._settings_page = _settings_v3
    web.merge_category_safely = _merge_category_safer
