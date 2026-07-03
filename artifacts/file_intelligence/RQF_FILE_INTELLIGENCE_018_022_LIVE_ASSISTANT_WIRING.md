# RQF File Intelligence 018-022 Live Assistant Wiring

## Summary

- Wired document-intake output into the live assistant conversation service before provider execution.
- Uploads containing `DOCUMENT_INTAKE_RESULT JSON:` now return deterministic proposal text from `document_conversation.py`.
- Confirmation/cancel commands are parsed deterministically from normal assistant text messages.
- Confirmation resolves against the latest stored `DOCUMENT_INTAKE_RESULT` in the same conversation, so v1 remains stateless at the document layer.

## Router Touch

- `src/samchat/assistant/router.py` was touched only in `create_message`.
- Exact live touch point: `create_message` now passes `document_action_router_executor` into `run_message_turn_with_pending`.
- The executor refuses non-read canonical actions and calls `execute_canonical_action(...)` only for `supported_read_actions()`.
- No endpoint, request model, response model, auth dependency, session dependency, upload endpoint, OCR path, webhook path, or provider selection path was changed.

## Live Flow Point

- Upload flow: `router.py:create_media_message` -> `upload_service.extract_text_from_media` -> `conversation_service.run_conversation_turn`.
- `run_conversation_turn` now detects `DOCUMENT_INTAKE_RESULT JSON:` and returns a deterministic rendered proposal.
- Text flow: `router.py:create_message` -> `conversation_service.run_message_turn_with_pending`.
- `run_message_turn_with_pending` now detects `CONFIRMAR accion <id>`, `CONFIRM action <id>`, `cancelar accion <id>`, and `cancel action <id>`.

## Rendering Behavior

- Upload-derived proposed actions are rendered with:
  - detected document type
  - summary
  - missing fields
  - questions
  - proposed action title
  - canonical action
  - proposed action id
  - confirmation and cancel commands

## Confirmation Behavior

- Confirmation validates through `document_confirmation.py`.
- Write-like actions remain blocked when `ASSISTANT_AGENT_WRITES_ENABLED=false`.
- Missing fields return `needs_clarification`.
- Wrong or unavailable proposed action ids fail closed.
- Cancel commands return canceled and do not execute.
- Read-only preview actions can use an injected action-router executor and are covered by a mocked action-router test.

## State Strategy

- Stateless v1.
- No new DB table, migration, model, session semantic, or persistence model was added.
- The latest intake envelope is read from existing assistant message content in the current conversation.

## Files Changed By This Task

- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_018_LIVE_WIRING_AUDIT.md`
- `artifacts/file_intelligence/RQF_FILE_INTELLIGENCE_018_022_LIVE_ASSISTANT_WIRING.md`
- `src/samchat/assistant/conversation_service.py`
- `src/samchat/assistant/document_confirmation.py`
- `src/samchat/assistant/document_conversation.py`
- `src/samchat/assistant/router.py`
- `tests/unit/test_assistant_document_live_wiring.py`

## Frozen UI

- Frozen assistant UI source/dist were untouched by this task.
- No edits were made to `goal-fest-page/src/pages/Assistant.tsx`.
- No edits were made to `goal-fest-page/dist`.

## Safety

- No direct DB writes were added for document action execution.
- Existing assistant message persistence is used only to store the user/assistant conversation turn, matching the existing assistant flow.
- No DB/auth/OCR/webhook/provider behavior was changed.
- No provider call is needed for deterministic document-intake rendering or command handling.
- No write adapter is called by the document confirmation path while writes are disabled.
- Read-only execution is allowed only through the injected action-router executor.
- No private reasoning or chain-of-thought is exposed; outputs are compact summaries, labels, proposed actions, caveats, and blocked reasons.

## Validation

- `python3 -m py_compile src/samchat/assistant/document_conversation.py src/samchat/assistant/document_confirmation.py src/samchat/assistant/upload_service.py src/samchat/assistant/router.py src/samchat/assistant/conversation_service.py tests/unit/test_assistant_document_live_wiring.py` - passed
- `./scripts/pytestw tests/unit/test_assistant_document_live_wiring.py` - 7 passed
- `./scripts/pytestw tests/unit/test_assistant_document_conversation_confirmation_loop.py` - 7 passed
- `./scripts/pytestw tests/unit/test_assistant_document_confirmation_execution_gate.py` - 6 passed
- `./scripts/pytestw tests/unit/test_assistant_upload_document_intake_integration.py` - 5 passed
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py` - 13 passed
- `git diff --check` on owned files - passed

## Caveats

- The wider worktree remains dirty from pre-existing work.
- `git diff --name-only` reports pre-existing modifications in forbidden areas such as gastos model/auth/webhook files; those were not edited by this task.
- Many file-intelligence modules and artifacts are untracked in this dirty worktree, so normal `git diff --stat` only shows tracked-file changes.

## Required Statement

File-derived proposed actions are visible in assistant conversation context, confirmation commands are parsed deterministically, writes remain blocked when disabled, read-only previews use the action-router path, and no DB/auth/OCR/webhook/provider behavior was changed.
