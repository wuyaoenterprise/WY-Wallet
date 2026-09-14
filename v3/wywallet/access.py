from __future__ import annotations

"""Temporary public-access experiment for Streamlit sleep diagnosis.

The V3 app-level password gate is intentionally disabled so we can verify
whether authenticated app traffic is related to Community Cloud hibernation.
Streamlit deployment visibility still controls whether the URL itself is public.
"""

PUBLIC_ACCESS_EXPERIMENT = True


def truthy_secret(name: str) -> bool:
    # Kept for import compatibility while the password experiment is active.
    return False


def configured_password() -> str:
    # Ignore WEB_ACCESS_PASSWORD during this experiment.
    return ""


def touch_access(*, stop_on_expired: bool = True) -> str:
    return "public"


def require_access() -> str:
    return "public"


def render_lock_button() -> None:
    # There is no local access session to lock while public-access testing is on.
    return None
