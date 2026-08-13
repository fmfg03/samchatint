# RQF-SAMCHAT-ASSISTANT-052J - Specialist Preview Renderer Contract

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Define the UI/chat-facing render contract for specialist business previews. The
assistant already produces `business_preview`; this slice turns that object into
stable sections that can be rendered consistently without exposing agent internals.

## Implemented

- Added `specialist_preview_renderer.py`.
- Added `SpecialistPreviewRender` and `SpecialistPreviewSection`.
- Added stable section ids:
  - `summary`
  - `proposed_changes`
  - `evidence`
  - `missing_evidence`
  - `steps`
  - `checks`
  - `authority`
- Added deterministic Markdown rendering for review/debugging.
- Added regression tests covering stable section order, evidence, missing
  evidence, supported changes, and blocked authority.
- Added renderer tests to the assistant scoped gate.

## Safety properties

- Rendering is read-only and formats an existing preview.
- The primary action is explicitly disabled.
- Authority is shown as blocked while approval remains required.
- No production UI route or write path is connected.

## Non-claims

- This does not yet render inside `/assistant`.
- This does not implement approval receipts.
- This does not execute any business action.
