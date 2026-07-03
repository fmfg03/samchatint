# RQF File Intelligence 023-025 Runtime Smoke and Provider Isolation

## Summary

- Added a focused runtime smoke test for the live document-intake conversation service path.
- The smoke uses provider sentinels that raise if the provider path is called.
- The deterministic upload proposal, confirm, cancel, missing-fields, no-envelope, and read-only preview paths all returned before provider execution.

## Verification Results

- Deterministic document-intake upload rendering bypasses provider calls.
- Confirmation and cancellation commands bypass provider calls.
- Write-like document actions remain blocked with `ASSISTANT_AGENT_WRITES_ENABLED=false`.
- Cancel commands do not call `action_router`.
- Read-only preview uses only the injected action-router executor.
- Missing fields return `needs_clarification` before provider execution.
- Missing stored intake envelope fails closed before provider execution.

## Files Changed By This Step

- `tests/unit/test_assistant_document_runtime_smoke.py`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_023_025_RUNTIME_SMOKE_AND_PROVIDER_ISOLATION.md`

## Router and Product Code

- `router.py` required no additional edits in this step.
- `conversation_service.py`, `document_conversation.py`, and `document_confirmation.py` required no edits in this step.
- Frozen assistant UI was untouched:
  - no edits to `goal-fest-page/src/pages/Assistant.tsx`
  - no edits to `goal-fest-page/dist`

## Safety Statement

- No DB model, migration, auth/session, OCR, webhook, provider, or direct adapter behavior was changed.
- No production/live DB was touched.
- No provider clients were called in the smoke tests.
- No write adapters were executed.
- Read-only preview execution was represented by an injected mocked action-router executor.

## Validation Commands

- `python3 -m py_compile src/samchat/assistant/conversation_service.py src/samchat/assistant/router.py src/samchat/assistant/document_conversation.py src/samchat/assistant/document_confirmation.py tests/unit/test_assistant_document_runtime_smoke.py` - passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py` - 6 passed
- `ASSISTANT_AGENT_RUNTIME_ENABLED=false ASSISTANT_AGENT_WRITES_ENABLED=false ASSISTANT_AGENT_SHADOW_ENABLED=false ./scripts/pytestw tests/unit/test_assistant_document_live_wiring.py` - 7 passed
- `ASSISTANT_AGENT_RUNTIME_ENABLED=false ASSISTANT_AGENT_WRITES_ENABLED=false ASSISTANT_AGENT_SHADOW_ENABLED=false ./scripts/pytestw tests/unit/test_assistant_document_conversation_confirmation_loop.py` - 7 passed
- `ASSISTANT_AGENT_RUNTIME_ENABLED=false ASSISTANT_AGENT_WRITES_ENABLED=false ASSISTANT_AGENT_SHADOW_ENABLED=false ./scripts/pytestw tests/unit/test_assistant_upload_document_intake_integration.py` - 5 passed
- `ASSISTANT_AGENT_RUNTIME_ENABLED=false ASSISTANT_AGENT_WRITES_ENABLED=false ASSISTANT_AGENT_SHADOW_ENABLED=false ./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py` - 13 passed

## Caveats

- The wider repository remains dirty from pre-existing work.
- The smoke verifies the live assistant conversation service seam with synthetic fixtures and hard provider sentinels; it does not execute writes or call live providers.
