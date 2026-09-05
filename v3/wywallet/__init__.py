"""WY Wallet V3 shared application package.

Compatibility shims for Gemini structured output and request timeouts.

Pydantic numeric constraints such as ``gt=0`` are emitted as JSON Schema
``exclusiveMinimum`` and are rejected by the current google-genai schema adapter.
The validators below enforce the same rules locally without exposing unsupported
range keywords to Gemini.

V2 did not impose a short client-side timeout, so slower receipt vision requests
were allowed to finish. V3 keeps a safety bound to avoid indefinite hangs, but uses
a 90-second HTTP timeout so normal slow Gemini responses are not cut off at 30s.
The existing retry wrapper in ``ai.py`` still handles transient upstream errors.
"""

from __future__ import annotations

from typing import Literal

import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, field_validator

from . import ai as _ai


class _ReceiptTransactionCompat(BaseModel):
    date: str | None = Field(default=None, description="Transaction date in YYYY-MM-DD if visible")
    item: str = Field(description="Short merchant or item name")
    category: str = Field(description="One category from the supplied category list")
    type: Literal["Expense", "Refund"] = "Expense"
    amount: float = Field(description="Positive absolute amount for this item")
    note: str = ""

    @field_validator("amount")
    @classmethod
    def _amount_must_be_positive(cls, value: float) -> float:
        value = float(value)
        if value <= 0:
            raise ValueError("amount must be greater than 0")
        return value


class _ReceiptResultCompat(BaseModel):
    merchant: str | None = Field(default=None, description="Merchant/store name if confidently visible")
    receipt_number: str | None = Field(default=None, description="Receipt/invoice number if confidently visible")
    transactions: list[_ReceiptTransactionCompat] = Field(default_factory=list)
    receipt_total: float | None = Field(default=None, description="Signed final payable total: purchases positive, pure refunds negative")
    tax: float = 0
    service_charge: float = 0
    discount: float = 0
    warnings: list[str] = Field(default_factory=list)

    @field_validator("tax", "service_charge", "discount")
    @classmethod
    def _metadata_must_be_non_negative(cls, value: float) -> float:
        value = float(value)
        if value < 0:
            raise ValueError("receipt metadata amounts must be non-negative")
        return value


@st.cache_resource(show_spinner=False)
def _get_ai_client_with_timeout() -> genai.Client:
    return genai.Client(
        api_key=st.secrets["GOOGLE_API_KEY"],
        http_options=types.HttpOptions(timeout=90_000),
    )


# ai.py resolves these module globals at call time, so replacing them here fixes
# Gemini structured output and prevents a stalled request from hanging forever
# without changing the rest of the finance AI pipeline.
_ai.ReceiptTransaction = _ReceiptTransactionCompat
_ai.ReceiptResult = _ReceiptResultCompat
_ai.get_ai_client = _get_ai_client_with_timeout
