# RQF_HARDENING_001_005 Closeout

Date: 2026-07-03

## Branch And Head

- Clean-pack branch: `rqf/hardening-001-005-clean`
- Clean-pack base: `c22c9b2f8`
- Base selection note: local committed refs do not combine `333a2ff44e495b09c494afbde3a86167470057fa` ancestry with tracked `copa_telmex_dashboard.py`; `c22c9b2f8` is the local committed live-runtime base containing the dashboard entrypoint.

## Files Changed By This Sprint

- `copa_telmex_dashboard.py`
- `src/devnous/gastos/routes/webhook_handler.py`
- `src/samchat/assistant/router.py`
- `telegram_roster_ocr_bot.py`
- `tests/unit/test_registration_review_security.py`
- `tests/unit/gastos/test_tocino_webhook_routes.py`
- `tests/unit/test_assistant_request_router_integration.py`
- `tests/unit/test_assistant_analyst_routing_integration.py`
- `tests/unit/test_assistant_action_router_contracts.py`
- `tests/unit/test_assistant_document_runtime_smoke.py`
- `artifacts/deployment/SAMCHAT_LIVE_RUNTIME_SOURCE_OF_TRUTH.md`
- `artifacts/repo_hygiene/SAMCHAT_GENERATED_AND_LEGACY_SURFACES_POLICY.md`
- `artifacts/hardening/RQF_HARDENING_001_005_CLOSEOUT.md`

## Config Guards Added

- Added explicit production-mode detection using `SAMCHAT_ENV`, `ENVIRONMENT`, `APP_ENV`, or `FASTAPI_ENV` values `production`, `prod`, or `live`.
- Added dashboard startup guard so explicit production mode without `DATABASE_URL` raises a startup `RuntimeError`.
- Preserved the existing local/dev database fallback when production mode is not explicitly set.
- Existing session-secret startup guard remains in place through `SESSION_SECRET_KEY`.

## Webhook Fail-Closed Behavior

- Tocino webhook now rejects payloads in explicit production mode when `TOCINO_WEBHOOK_SECRET` is missing.
- Rejection happens before JSON payload processing and before DB write-path execution.
- Configured webhook secrets continue to use the existing HMAC verification behavior.
- Secret values are not printed or persisted.

## Tests Added Or Restored

- Restored `tests/unit/test_assistant_request_router_integration.py`.
- Restored `tests/unit/test_assistant_analyst_routing_integration.py`.
- Added thin compatibility validation tests for action-router and deterministic runtime smoke because the clean live-runtime base did not include those files.
- Added production/dev DB guard tests.
- Added production missing-secret and configured-secret Tocino webhook tests.

## Explicit Non-Changes

No DB schema, migration, auth workflow semantics, OCR execution, provider call, external webhook call, or product feature workflow was changed by this sprint.
