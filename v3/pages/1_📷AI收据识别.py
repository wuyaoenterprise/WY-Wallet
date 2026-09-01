from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "v2"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

runpy.run_path(str(CORE / "pages" / "1_📷AI收据识别.py"), run_name="__main__")
