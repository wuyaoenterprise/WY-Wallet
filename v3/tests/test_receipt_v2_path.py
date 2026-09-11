from __future__ import annotations

import json
from pathlib import Path

from wywallet import _ReceiptResultCompat

ROOT = Path(__file__).resolve().parents[2]


def test_receipt_schema_has_no_google_adapter_numeric_bounds():
    packed = json.dumps(_ReceiptResultCompat.model_json_schema(), sort_keys=True)
    assert "exclusiveMinimum" not in packed
    assert '"minimum"' not in packed


def test_receipt_runtime_uses_plain_json_without_short_timeout():
    source = (ROOT / "v3" / "wywallet" / "__init__.py").read_text(encoding="utf-8")
    assert "response_mime_type=\"application/json\"" in source
    assert "response_schema=" not in source
    assert "HttpOptions(timeout" not in source
    assert "item、note、warnings 默认输出简体中文" in source
    assert "商家/品牌专有名称尽量保留收据原文" in source
    assert "Gemini 当前繁忙" in source


def test_receipt_page_keeps_compact_table_and_full_state_cleanup():
    source = (ROOT / "v3" / "pages" / "receipt.py").read_text(encoding="utf-8")
    assert "已恢复 V2 的紧凑表格方式" in source
    assert "_clear_target_editor_state" in source
    assert "receipt_editor_release_" in source
    assert 'a, b, c, d, e = st.columns(5' in source
