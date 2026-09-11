from __future__ import annotations

from typing import Any

from . import db
from .snapshot import _normalize_payload, _payload_dict


def full_backup_snapshot() -> dict[str, Any]:
    """Fetch the whole ledger in one PostgreSQL statement/MVCC snapshot."""
    response = db.get_client().rpc("wy_wallet_backup_snapshot").execute()
    return _normalize_payload(_payload_dict(response.data))


def database_revision() -> tuple[int, str]:
    response = db.get_client().rpc("wy_wallet_get_ledger_revision").execute()
    data = response.data or []
    row: dict[str, Any] = {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        row = data[0]
    elif isinstance(data, dict):
        row = data
    return int(row.get("revision") or 0), str(row.get("updated_at") or "")
