from __future__ import annotations

from types import SimpleNamespace

import pytest

import wywallet.ai as ai
import wywallet.model_router as router
from wywallet.config import GEMINI_MODEL, GEMINI_MODELS


class _FakeModels:
    def __init__(self, outcomes: dict[str, object]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    def generate_content(self, *, model: str, **kwargs):
        self.calls.append(model)
        outcome = self.outcomes.get(model, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, list):
            current = outcome.pop(0) if outcome else "ok"
            if isinstance(current, Exception):
                raise current
            outcome = current
        return SimpleNamespace(text=str(outcome), parsed=None)


class _FakeClient:
    def __init__(self, outcomes: dict[str, object]):
        self.models = _FakeModels(outcomes)


def test_flash_fallback_order_is_newest_to_oldest():
    assert GEMINI_MODEL == "gemini-3.8-flash"
    assert GEMINI_MODELS == (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )
    assert router.model_fallback_chain() == GEMINI_MODELS


def test_daily_quota_falls_through_all_flash_models_without_same_model_retries():
    quota = RuntimeError(
        "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier free_tier_requests"
    )
    client = _FakeClient({
        "gemini-3.8-flash": quota,
        "gemini-3.7-flash": quota,
        "gemini-3.6-flash": quota,
        "gemini-3.5-flash": "fallback-ok",
    })

    response = router.generate_content_with_fallback(
        lambda: client,
        model=GEMINI_MODEL,
        contents="test",
    )

    assert response.text == "fallback-ok"
    assert client.models.calls == list(GEMINI_MODELS)


def test_transient_service_error_retries_then_moves_to_next_model(monkeypatch):
    monkeypatch.setattr(router.time, "sleep", lambda *_: None)
    client = _FakeClient({
        "gemini-3.8-flash": [
            RuntimeError("503 UNAVAILABLE high demand"),
            RuntimeError("503 UNAVAILABLE high demand"),
            RuntimeError("503 UNAVAILABLE high demand"),
        ],
        "gemini-3.7-flash": "recovered",
    })

    response = router.generate_content_with_fallback(
        lambda: client,
        model=GEMINI_MODEL,
        contents="test",
    )

    assert response.text == "recovered"
    assert client.models.calls == ["gemini-3.8-flash"] * 3 + ["gemini-3.7-flash"]


def test_invalid_request_does_not_burn_fallback_quota():
    client = _FakeClient({"gemini-3.8-flash": RuntimeError("400 INVALID_ARGUMENT bad schema")})

    with pytest.raises(RuntimeError, match="INVALID_ARGUMENT"):
        router.generate_content_with_fallback(
            lambda: client,
            model=GEMINI_MODEL,
            contents="test",
        )

    assert client.models.calls == ["gemini-3.8-flash"]


def test_package_routes_existing_ai_calls_through_model_fallback():
    assert ai._generate_content_with_retry.__name__ == "_generate_content_with_model_fallback"
