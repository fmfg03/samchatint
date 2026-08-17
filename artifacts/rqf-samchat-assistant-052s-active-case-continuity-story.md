# RQF-SAMCHAT-ASSISTANT-052S - Active Case Continuity Story

Status: IMPLEMENTED_LOCAL

## User story

As a SamChat user working through a specialist preview, I want to see whether the assistant is operating inside an active case or starting from an isolated request, so I can understand what will be resumed, what context is scoped, and what still requires explicit authorization.

## Product intent

This slice makes the assistant more Claude-Code-like: the workspace should show the current operational thread, not only the immediate message. It bridges the existing conversation metadata and active tournament case pointer into the specialist preview surface.

## Scope

- Read current conversation metadata in-process.
- Surface module, tournament key, and active tournament goal case pointer when present.
- Add continuity as a read-only card/source/step in specialist preview payloads.
- Preserve all preview-only authority boundaries.

## Out of scope

- Creating or mutating case pointers.
- Resuming or applying proposals automatically.
- Cross-conversation case switching.
- New frontend components beyond the existing card/source/step contracts.

## Acceptance criteria

- Preview payload includes `continuity_context`.
- If an active tournament case exists, it exposes case id, version and status.
- If no active case exists, it says so explicitly without failing the preview.
- Workspace card/source/step distinguish active continuity from historical memory.
- No writes, provider calls or action enablement are introduced.

## Closeout

Implemented locally in branch `codex/rqf-assistant-artifact-audit-001`.

Result:

- Specialist previews now expose current conversation continuity.
- Active tournament goal cases are visible as read-only context.
- No active case is explicitly reported instead of silently omitted.
- Authority remains blocked; continuity informs but does not authorize.
