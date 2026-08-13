# RQF-SAMCHAT-ASSISTANT-052K - Assistant Preview Surface

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Expose the specialist business preview renderer through the assistant response
surface without enabling writes or requiring the frontend to understand agent
internals.

## Implemented

- Added deterministic `specialist_preview_surface.py`.
- Added explicit task-id detection for requests such as
  `Muestra preview especialista SAMCHAT-CXC-COLLECTION-001`.
- Added `preview_render` to `MessageResponse` as an optional structured payload.
- Wired the surface into conversation turns before provider fallback.
- Persisted the rendered assistant message in the conversation.
- Added unit/integration coverage and assistant scoped gate coverage.

## Safety properties

- Requires explicit preview/specialist wording plus a known `SAMCHAT-*` task id.
- Uses seed specialist benchmarks only; unsupported task ids fail closed.
- Provider is not called.
- `primary_action_enabled` remains false.
- `execution_status` remains `not_executed`.
- No production write path is connected.

## Non-claims

- This does not yet render custom cards in the `/assistant` frontend.
- This does not execute approvals or actions.
- This does not run specialist agents over live production cases.
