# RQF-SAMCHAT-ASSISTANT-052O ? Specialist Preview Live Read-only Context

Status: IMPLEMENTED_LOCAL

## Objective

Move specialist previews from message-only context hints to optional live SamChat context lookup, without enabling writes or authority.

## Boundary

- Read-only lookup only.
- No provider call.
- No write action.
- No authorization implication.
- Preview remains deterministic and inert.

## Implemented

- New resolver: `src/samchat/assistant/specialist_live_context.py`.
- Resolves user-mentioned references against SamChat DB when a real session is available:
  - `S-` / `I-` document references via `Documento.numero_referencia`.
  - `O-` expense references via `ExpenseReport.numero_referencia`.
  - `REF <n>` via `Documento.referencia_operaciones`.
  - CFDI UUID/prefix via `CFDIReport.cfdi_uuid`, `ExpenseReport.cfdi_uuid_manual`, and `Documento.cfdi_uuid_manual`.
- Adds `Contexto encontrado` section to assistant specialist previews.
- Persists `live_context` in assistant message payload.
- Adds `live_context` to tool trace.
- Falls back safely when no DB session or no reference hints are available.

## Verification

Commands:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py::test_specialist_preview_surface_attaches_read_only_live_context \
  -q
```

Result: 11 passed.

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_request_router_integration.py \
  tests/unit/test_assistant_specialist_agents.py \
  tests/unit/test_assistant_specialist_orchestrator.py \
  -q
```

Result: 34 passed.

## Claim

Specialist previews can now show what SamChat found for the references in the user's prompt, while preserving the read-only specialist-preview boundary.
