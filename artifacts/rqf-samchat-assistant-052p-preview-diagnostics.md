# RQF-SAMCHAT-ASSISTANT-052P ? Specialist Preview Operational Diagnostics

Status: IMPLEMENTED_LOCAL

## Objective

After a specialist preview understands and resolves context, add a deterministic read-only diagnosis explaining whether the assistant has enough context to continue preparing a business preview.

## Boundary

- Read-only diagnostic only.
- No provider call.
- No writes.
- No authority creation.
- No execution path unlocked.

## Implemented

- `build_specialist_preview_diagnostics(...)` creates a deterministic diagnostic bundle from:
  - task id,
  - understood context,
  - live read-only context.
- `render_specialist_preview_diagnostics_markdown(...)` renders a `Diagnostico operativo` section.
- Conversation response now persists `diagnostics` in the assistant tool payload.
- Tool trace now includes `diagnostics` under `specialist_preview_surface` and `result`.

## Diagnostic categories

- readiness: `ready_for_read_only_preview` or `needs_more_context`.
- findings.
- missing context.
- deterministic risks.
- next read-only step.

## Verification

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py::test_specialist_preview_surface_attaches_read_only_live_context \
  -q
```

Result: 13 passed.

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py \
  tests/unit/test_assistant_specialist_agents.py \
  tests/unit/test_assistant_specialist_orchestrator.py \
  -q
```

Result: 36 passed.

## Claim

Specialist previews now explain not only what was understood and found, but what the assistant can safely do next in read-only mode and what context is still missing.
