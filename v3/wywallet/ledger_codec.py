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
