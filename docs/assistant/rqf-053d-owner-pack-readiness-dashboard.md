# RQF-053D - Owner Pack Readiness Dashboard / respuesta navegable

Status: IMPLEMENTED_PENDING_PR
Date: 2026-08-24
Branch: `codex/rqf-053d-owner-pack-dashboard`

## Intent

Slice 2B converts Owner Pack readiness into a minimal navigable dashboard. The goal is not to prove the pack is complete; the goal is to show, clearly and safely, what exists, what is missing, which sources are available, and what should be asked next.

## User-facing closure

The dashboard includes these sections every time:

1. Torneo / contexto.
2. Entity folder.
3. National phase.
4. Marketing.

Each section exposes:

- status;
- coverage score;
- supported and missing field counts;
- available sources;
- missing items;
- next action;
- next questions when applicable.

## Authority boundary

This slice is read-only. It does not create folders, write artifacts, notify users, or claim operational completion.

The dashboard can be used by the assistant to answer executive questions such as:

- "Tenemos listo el pack del dueno?"
- "Que falta para ver el pack del torneo?"
- "Que fuentes tenemos disponibles para la carpeta de entidad?"

A truthful answer may be: "no hay informacion suficiente todavia".

## Implementation

- `src/samchat/assistant/owner_pack_readiness_dashboard.py`
- Router read tool: `assistant_owner_pack_readiness_dashboard`
- Authenticated endpoint: `/api/assistant/owner-pack/readiness-dashboard`
- Tests: `tests/unit/test_assistant_owner_pack_readiness_dashboard.py`

## Verification

Focused suite run:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_owner_pack_readiness_dashboard.py \
  tests/unit/test_assistant_owner_pack_readiness.py \
  tests/unit/test_assistant_tool_registry.py \
  tests/unit/test_assistant_executive_answer_renderer.py -q
```

Result at implementation time: 17 passed.
