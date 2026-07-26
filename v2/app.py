"""Stable Streamlit entry point for WY Wallet V2.

The implementation lives in app_rich.py. runpy executes it on every Streamlit
rerun, avoiding normal Python module caching after buttons or filters change.
"""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).with_name("app_rich.py")), run_name="__main__")
