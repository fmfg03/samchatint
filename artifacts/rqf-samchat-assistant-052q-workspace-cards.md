# RQF-SAMCHAT-ASSISTANT-052Q ? Specialist Preview Workspace Cards

Status: IMPLEMENTED_LOCAL

## Objective

Prepare the backend contract for a future Claude-Code-like assistant workspace UI without changing write authority or requiring the UI revamp in this slice.

## Implemented

- Added `build_specialist_preview_workspace_cards(...)`.
- Conversation specialist previews now attach `workspace_cards` to:
  - `tool_trace[0].specialist_preview_surface.workspace_cards`
  - `tool_trace[0].result.workspace_cards`
  - persisted assistant `tool_payload.workspace_cards`

## Card contract

The preview now exposes five ordered cards:

1. `understood_context` ? what the assistant detected in the user's message.
2. `live_context` ? what SamChat found read-only in DB.
3. `operational_diagnostics` ? readiness, findings, gaps, risks, next step.
4. `business_preview` ? deterministic specialist preview payload.
5. `authority_boundary` ? blocked execution / approval requirement.

## Boundary

- No writes.
- No provider call.
- No execution unlock.
- No authority creation.
- UI-ready structure only.

## Verification

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py::test_specialist_preview_surface_attaches_read_only_live_context \
  -q
```

Result: 14 passed.

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py \
  tests/unit/test_assistant_specialist_agents.py \
  tests/unit/test_assistant_specialist_orchestrator.py \
  -q
```

Result: 37 passed.

## Claim

The assistant backend now emits a stable structured card contract suitable for the future operator workspace UI, while preserving the current read-only specialist-preview boundary.
