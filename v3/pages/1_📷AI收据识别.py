from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED_WEB_CORE = ROOT / "v2"
if str(SHARED_WEB_CORE) not in sys.path:
    sys.path.insert(0, str(SHARED_WEB_CORE))

# The V3 branch keeps one tested web-core package instead of duplicating finance
# logic between V2- and V3-named folders. This entrypoint always executes the
# hardened V3.1 receipt implementation from that shared package; branding,
# access gate and build/version values are all V3.
runpy.run_path(str(SHARED_WEB_CORE / "pages" / "1_📷AI收据识别.py"), run_name="__main__")
