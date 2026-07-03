# RQF File Intelligence 018 Live Wiring Audit

## Live Assistant Flow

- Runtime target remains `copa_telmex_dashboard.py`.
- `/assistant` is served by the live FastAPI app frontend asset path; the API surface for assistant messages is `src/samchat/assistant/router.py`.
- Text messages enter `router.py:create_message`, then call `run_message_turn_with_pending(...)` from `src/samchat/assistant/conversation_service.py`.
- Uploaded media enters `router.py:create_media_message`, then calls `extract_text_from_media(...)` from `src/samchat/assistant/upload_service.py`.
- `extract_text_from_media(...)` prepends a compact `DOCUMENT_INTAKE_RESULT JSON:` block to the extracted upload context.
- The media endpoint currently passes that extracted context to `run_conversation_turn(...)`, which previously delegated straight to `_assistant_turn(...)`.

## Current Deterministic Consumption

- Before this wiring pass, `DOCUMENT_INTAKE_RESULT` reached the assistant prompt/context but was not deterministically rendered by the live conversation service.
- Confirmation commands such as `CONFIRMAR accion <id>` enter through the same text message endpoint as normal assistant messages.
- Existing pending-run confirmation is separate and is handled by `/confirm` plus `run_message_turn_with_pending(...)`.

## Safe State Strategy

- No new database table is needed for v1.
- Upload-derived proposed action envelopes can remain stateless because the same compact `DOCUMENT_INTAKE_RESULT` block is stored in the existing conversation message content.
- Confirmation commands can resolve against the latest stored `DOCUMENT_INTAKE_RESULT` in the current conversation.
- This keeps document confirmation scoped to existing conversation storage without changing auth, session, model, migration, OCR, webhook, provider, or UI behavior.

## Proposed Minimal Wiring Point

- Add deterministic document-intake handling in `conversation_service.py`, before provider execution.
- For uploads with `DOCUMENT_INTAKE_RESULT`, render the compact proposal text from `document_conversation.py` and return it as the assistant message.
- For confirmation/cancel text commands, parse through `document_conversation.py`, resolve the latest intake envelope from existing conversation messages, and validate via `document_confirmation.py`.
- Write-like confirmations remain blocked when writes are disabled.
- Read-only previews use an injected action router executor in tests and can be routed only through that seam.

## Scope Guard

- Frozen assistant UI files are not required and should remain untouched.
- `router.py` does not need route-shape changes if `conversation_service.py` handles the live message/media seam.
- New code must not call providers, OCR, DB write adapters, webhooks, or direct DB mutation paths outside existing message persistence.
