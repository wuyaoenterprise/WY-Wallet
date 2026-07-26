"""Stable Streamlit entry point for WY Wallet V2.

The implementation lives in app_rich.py. runpy executes it on every Streamlit
rerun, avoiding normal Python module caching after buttons or filters change.
"""

import runpy
from pathlib import Path

# Keep the Streamlit Cloud main-file path stable while the implementation evolves.
runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
