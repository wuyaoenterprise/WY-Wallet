from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "v2"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from wywallet.web import run


if __name__ == "__main__":
    run()
