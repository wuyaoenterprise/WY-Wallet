from __future__ import annotations

import re
from typing import Any

from .config import EXPENSE, INCOME, RECEIPT_META_PREFIX, REFUND, REFUND_DB_MARKER

REFUND_SUBTYPES = {"customer_refund", "receipt_discount"}
EXPENSE_SUBTYPES = {"receipt_tax", "receipt_service_charge"}
ALLOWED_FLOW_SUBTYPES = REFUND_SUBTYPES | EXPENSE_SUBTYPES
_RECEIPT_RE = re.compile(r"\[WY_RECEIPT:([A-Za-z0-9_-]{6,64})\]")


def decode_legacy_note(note: object) -> tuple[str, bool, str]:
    """Return user-visible note plus legacy refund/receipt metadata.

    This keeps repair/edit UIs from exposing internal markers while preserving
    backward compatibility with rows written before structured metadata existed.
    """
    raw = str(note or "")
    marker_refund = False
    if raw.startswith(REFUND_DB_MARKER):
        marker_refund = True
        raw = raw[len(REFUND_DB_MARKER):].lstrip()
    match = _RECEIPT_RE.search(raw)
    receipt_id = match.group(1) if match else ""
    if match:
        raw = _RECEIPT_RE.sub("", raw).strip()
    return raw, marker_refund, receipt_id


def _identity_date(value: object) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else text


def _identity_item(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _identity_amount(value: object) -> float | None:
    try:
        return round(float(value), 2)
    except Exception:
        return None


def receipt_identity_changed(original: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Whether an edit changes the semantic identity of a receipt line.

    Category and note edits intentionally do not detach a receipt. Date, item,
    logical type and amount do, because those fields participate in duplicate and
    provenance semantics.
    """
    return (
        _identity_date(original.get("date")) != _identity_date(candidate.get("date"))
        or _identity_item(original.get("item")) != _identity_item(candidate.get("item"))
        or str(original.get("type") or "") != str(candidate.get("type") or "")
        or _identity_amount(original.get("amount")) != _identity_amount(candidate.get("amount"))
    )


def detach_receipt_if_identity_changed(
    original: dict[str, Any],
    logical: dict[str, Any],
    existing_subtype: str | None = None,
) -> tuple[dict[str, Any], str | None, bool]:
    """Detach stale receipt provenance when a receipt-linked row is re-authored."""
    result = dict(logical)
    receipt_id = str(original.get("receipt_id") or result.get("receipt_id") or "").strip()
    subtype = str(existing_subtype or "").strip() or None
    if not receipt_id or not receipt_identity_changed(original, result):
        return result, subtype, False

    result["receipt_id"] = ""
    if subtype and subtype.startswith("receipt_"):
        subtype = None
    return result, subtype, True


def physical_payload(logical: dict[str, Any], existing_subtype: str | None = None) -> dict[str, Any]:
    """Encode a logical V3 transaction for the legacy Expense/Income DB schema.

    Structured receipt_id/flow_subtype are the primary metadata. The legacy
    refund note marker remains for backwards compatibility with older readers.
    """
    payload = dict(logical)
    subtype = str(existing_subtype or payload.pop("flow_subtype", "") or "").strip() or None
    receipt_id = str(payload.get("receipt_id") or "").strip() or None
    note = str(payload.get("note") or "").strip()
    tx_type = str(payload.get("type") or "")

    if tx_type == REFUND:
        payload["type"] = INCOME
        if not note.startswith(REFUND_DB_MARKER):
            note = f"{REFUND_DB_MARKER} {note}".rstrip()
        if subtype not in REFUND_SUBTYPES:
            subtype = "customer_refund"
    elif tx_type == EXPENSE:
        if subtype not in EXPENSE_SUBTYPES:
            subtype = None
    else:
        subtype = None

    payload["note"] = note
    payload["receipt_id"] = receipt_id
    payload["flow_subtype"] = subtype
    return payload


def logical_type(raw_type: str, flow_subtype: str | None, already_logical_refund: bool = False) -> str:
    subtype = str(flow_subtype or "").strip()
    if already_logical_refund:
        return REFUND
    if str(raw_type) == INCOME and subtype in REFUND_SUBTYPES:
        return REFUND
    return str(raw_type)
