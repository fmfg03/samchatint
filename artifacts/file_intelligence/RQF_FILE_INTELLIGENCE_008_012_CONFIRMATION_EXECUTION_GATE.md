# RQF File Intelligence 008-012 Confirmation Execution Gate

## Summary

Added a deterministic confirmation gate for document-intake proposed actions.

The gate validates:

- `intake_id`
- stable proposed action ID
- canonical action name
- payload hash
- explicit confirmation text
- supported action surface
- write/read action boundary
- write feature availability

## Files Changed

- `src/samchat/assistant/document_confirmation.py`
- `tests/unit/test_assistant_document_confirmation_execution_gate.py`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_008_012_CONFIRMATION_EXECUTION_GATE.md`

## Contract Implemented

The confirmation result object includes:

- `confirmation_id`
- `intake_id`
- `proposed_action_id`
- `canonical_action`
- `confirmed`
- `executed`
- `status`
- `blocked_reason`
- `execution_result_summary`
- `safety`

Safety fields include:

- `used_action_router`
- `direct_write_attempted`
- `requires_human_review`

## Behavior Verified

- Proposed file-derived actions can be confirmed.
- Write-like actions remain blocked when writes are disabled.
- Read-only preview actions can execute only through an injected action-router executor.
- Payload tampering fails closed.
- Unsupported canonical actions fail closed.
- Unknown documents generate no executable proposed action.
- If action-router execution raises/rejects, the gate returns rejected and does not claim execution.

## Confirmation-Gating Result

- CFDI link action confirmed while writes disabled:
  - `confirmed=true`
  - `executed=false`
  - `status=blocked`
  - `blocked_reason=writes_disabled`
  - no executor call

- Payment registration confirmed while writes disabled:
  - `confirmed=true`
  - `executed=false`
  - `blocked_reason=writes_disabled`

- Read-only accounting preview confirmed:
  - action-router executor mock called
  - `executed=true`
  - `safety.used_action_router=true`

## Validation Commands And Results

- `PYTHONPYCACHEPREFIX=/tmp/rqf-file-intelligence-008-pycache python3 -m py_compile src/samchat/assistant/document_confirmation.py`: passed.
- `PYTHONPYCACHEPREFIX=/tmp/rqf-file-intelligence-008-pycache python3 -m py_compile src/samchat/assistant/document_action_planner.py`: passed.
- `./scripts/pytestw tests/unit/test_assistant_document_confirmation_execution_gate.py`: 6 passed.
- `./scripts/pytestw tests/unit/test_assistant_document_confirmation.py`: 4 passed.
- `./scripts/pytestw tests/unit/test_assistant_upload_document_intake_integration.py`: 5 passed.
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py`: 13 passed.
- `git diff --check` on owned files: passed.

## Safety Statement

Execution goes through action_router only. The new confirmation code does not write directly to DB tables, does not change auth/session behavior, does not touch workflow/OCR/webhook/provider paths, and does not invoke production/live APIs.

Frozen Assistant UI was untouched:

- `goal-fest-page/src/pages/Assistant.tsx` was not edited.
- `goal-fest-page/dist` was not edited or regenerated.
