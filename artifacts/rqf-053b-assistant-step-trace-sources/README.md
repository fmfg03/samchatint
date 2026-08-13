# RQF-053B ? Assistant step trace + sources panel

Status: IMPLEMENTED_DEPLOYED_STATIC_ASSETS

## Scope

This slice makes specialist-preview assistant responses explain their work in a Claude-Code-like way:

- deterministic step trace;
- source panel;
- read-only context provenance;
- authority boundary remains blocked.

## Backend

Added `src/samchat/assistant/assistant_workspace_trace.py`.

Specialist preview responses now attach:

- `step_trace`
- `source_panel`

under both `tool_trace[0].specialist_preview_surface` and persisted assistant `tool_payload`.

## Frontend

Patched external frontend source:

- `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

Deployment target:

- `/srv/samchat/current/goal-fest-page/dist`

Built asset(s):

- Assistant-BZyrMME1.js

## Verification

- `pytest tests/unit/test_assistant_specialist_preview_surface.py -q` ? 14 passed.
- `npm run build` in `/srv/samchat/archive/projects/goal-fest-page`.
- Active Assistant asset contains `Pasos de trabajo` and `Fuentes usadas` markers.

## Boundary

No writes, no provider changes, no authority changes, no approval semantics added.
