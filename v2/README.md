# WY Wallet Web Core (V3 branch)

On `agent/wy-wallet-v3`, this directory is the single shared implementation package used by the V3 Streamlit deployment. Its historical `v2/` path is retained only to avoid a risky duplicate-code migration; the active product identity, version, access policy and deployment entrypoint are V3.

## Active deployment

- Branch: `agent/wy-wallet-v3`
- Main file: `v3/app.py`
- Requirements: `v3/requirements.txt`
- Current build: `2026.09.01-v3.1.0 · v3-final-hardening-r1`
- Timezone: `Asia/Kuala_Lumpur`
- AI: `gemini-3.7-flash` through `google-genai`

The root production `app.py` and root `requirements.txt` remain untouched.

## Security

V3 is fail-closed. Configure `WEB_ACCESS_PASSWORD`, or only when Streamlit itself already enforces private platform access set `ALLOW_UNPROTECTED_ACCESS = true`. Without one of those protections the app stops before reading or displaying the ledger.

See `v3/README.md` for the current deployment and feature documentation.
