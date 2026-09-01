from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED_WEB_CORE = ROOT / "v2"
V3_ROOT = ROOT / "v3"
for path in (SHARED_WEB_CORE, V3_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v3_overrides import apply_overrides, expire_access_session_if_needed, render_session_controls

apply_overrides()
expire_access_session_if_needed()
runpy.run_path(str(SHARED_WEB_CORE / "pages" / "1_📷AI收据识别.py"), run_name="__main__")
render_session_controls()
