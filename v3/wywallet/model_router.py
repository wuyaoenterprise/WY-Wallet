from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .config import AI_RETRY_ATTEMPTS, GEMINI_MODEL, GEMINI_MODELS


def _error_text(exc: Exception) -> str:
    return str(exc).casefold()


def _is_quota_error(exc: Exception) -> bool:
    text = _error_text(exc)
    return any(token in text for token in ["429", "resource_exhausted", "resource exhausted", "quota"])


def _is_model_unavailable_error(exc: Exception) -> bool:
    text = _error_text(exc)
    return "model" in text and any(token in text for token in ["404", "not found", "not available", "unsupported model"])


def _is_transient_error(exc: Exception) -> bool:
    text = _error_text(exc)
    return any(token in text for token in [
        "429", "503", "502", "504", "resource_exhausted", "resource exhausted",
        "unavailable", "high demand", "timeout", "timed out", "deadline", "temporar",
    ])


def model_fallback_chain(requested_model: str | None = None) -> tuple[str, ...]:
    requested = str(requested_model or GEMINI_MODEL).strip()
    if requested in GEMINI_MODELS:
        start = GEMINI_MODELS.index(requested)
        return tuple(GEMINI_MODELS[start:])
    return (requested,)


def generate_content_with_fallback(client_getter: Callable[[], Any], **kwargs):
    """Generate with ordered Gemini Flash fallback while preserving caller config.

    Quota/rate-limit errors move immediately to the next configured model because
    those limits are model-specific in the Gemini API. Other transient service
    errors keep the existing retry behavior before falling back. Invalid requests
    and schema/programming errors fail immediately instead of burning more quota.
    """
    request = dict(kwargs)
    requested_model = str(request.pop("model", GEMINI_MODEL))
    models = model_fallback_chain(requested_model)
    last: Exception | None = None

    for model in models:
        for attempt in range(AI_RETRY_ATTEMPTS):
            try:
                return client_getter().models.generate_content(model=model, **request)
            except Exception as exc:
                last = exc

                # Daily/RPM quota and unavailable-model failures are better served
                # by trying the next model immediately rather than retrying the same
                # exhausted model and wasting time.
                if _is_quota_error(exc) or _is_model_unavailable_error(exc):
                    break

                if not _is_transient_error(exc):
                    raise

                if attempt >= AI_RETRY_ATTEMPTS - 1:
                    break
                time.sleep(1.0 * (2 ** attempt))

    raise last or RuntimeError("Gemini request failed across all configured models")
