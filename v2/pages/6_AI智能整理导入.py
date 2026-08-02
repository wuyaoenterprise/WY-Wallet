"""Streamlit page loader for the Gemini AI import workflow.

The full implementation is kept in ``v2/ai_import_impl.py`` and imported as a
real Python module. Streamlit executes files inside ``pages/`` with a dynamic
namespace; importing the implementation normally lets Pydantic resolve nested
model annotations reliably when generating the Gemini response schema.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


V2_DIR = Path(__file__).resolve().parents[1]
if str(V2_DIR) not in sys.path:
    sys.path.insert(0, str(V2_DIR))

MODULE_NAME = "ai_import_impl"
if MODULE_NAME in sys.modules:
    importlib.reload(sys.modules[MODULE_NAME])
else:
    importlib.import_module(MODULE_NAME)
