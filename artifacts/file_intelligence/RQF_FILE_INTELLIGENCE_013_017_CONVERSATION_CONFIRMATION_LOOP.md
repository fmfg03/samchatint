# RQF File Intelligence 013-017 Conversation Confirmation Loop

## Summary

Added a stateless deterministic conversation loop for document-intake proposed actions.

New helper module:

- `src/samchat/assistant/document_conversation.py`

The helper can:

- Render a compact assistant-facing intake summary.
- Expose detected document type, summary, missing fields, questions, proposed action title, canonical action, proposed action ID, and confirmation/cancel commands.
- Parse deterministic confirmation commands.
- Parse deterministic cancellation commands.
- Validate proposed action IDs against the compact intake envelope.
- Block confirmation when missing fields remain.
- Route confirmed read-only actions through an injected action-router executor.
- Route write-like actions through the existing confirmation gate, which blocks when writes are disabled.

## Command Forms

Accepted confirmation forms:

- `CONFIRMAR accion <proposed_action_id>`
- `CONFIRMAR acción <proposed_action_id>`
- `CONFIRM action <proposed_action_id>`

Accepted cancellation forms:

- `cancelar accion <proposed_action_id>`
- `cancelar acción <proposed_action_id>`
- `cancel action <proposed_action_id>`

## Behavior Verified

- File-derived proposed actions are visible in assistant conversation context.
- Confirmation commands are parsed deterministically.
- Cancellation returns canceled state and does not execute.
- Wrong or tampered action IDs fail closed.
- Missing fields prevent confirmation and produce a clarification message.
- Writes remain blocked when disabled.
- Read-only previews use the injected action_router path.
- No chain-of-thought/private reasoning is emitted.

## Validation Commands And Results

- `PYTHONPYCACHEPREFIX=/tmp/rqf-file-intelligence-013-pycache python3 -m py_compile src/samchat/assistant/document_conversation.py`: passed.
- `PYTHONPYCACHEPREFIX=/tmp/rqf-file-intelligence-013-pycache python3 -m py_compile src/samchat/assistant/document_confirmation.py`: passed.
- `./scripts/pytestw tests/unit/test_assistant_document_conversation_confirmation_loop.py`: 7 passed.
- `./scripts/pytestw tests/unit/test_assistant_document_confirmation_execution_gate.py`: 6 passed.
- `./scripts/pytestw tests/unit/test_assistant_upload_document_intake_integration.py`: 5 passed.
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py`: 13 passed.
- `git diff --check` on owned files: passed.

## Files Changed

- `src/samchat/assistant/document_conversation.py`
- `tests/unit/test_assistant_document_conversation_confirmation_loop.py`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_013_017_CONVERSATION_CONFIRMATION_LOOP.md`

## Safety Statement

No DB/auth/OCR/webhook/provider behavior was changed. No router endpoints were changed. No DB tables or migrations were added. The helper is stateless and requires callers to provide the compact intake envelope.

Frozen Assistant UI was untouched:

- `goal-fest-page/src/pages/Assistant.tsx` was not edited.
- `goal-fest-page/dist` was not edited or regenerated.
