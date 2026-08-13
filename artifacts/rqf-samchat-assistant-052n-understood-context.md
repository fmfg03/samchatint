# RQF-SAMCHAT-ASSISTANT-052N - Specialist Preview Understood Context

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Make natural-language specialist previews auditable before live retrieval. When a
user asks SamChat to prepare or review a business case, the assistant now shows
what references it understood from the user's message before rendering the inert
specialist preview.

## Implemented

- Added deterministic `extract_specialist_preview_understood_context()`.
- Extracts only user-message hints:
  - document references such as `S-2600071`, `I-991520`, `O-26000312`;
  - operations references such as `REF 28`;
  - UUID/CFDI prefixes such as `669DBF39`;
  - account codes such as `1150-001-001`;
  - domain hints such as AMEX, CxC, CFDI, torneo, presupuesto;
  - known entity hints used in current seed previews.
- Adds a `Contexto entendido` section to the assistant message.
- Persists `understood_context` in the assistant message tool payload.
- Adds `understood_context` to specialist preview tool trace and result payload.

## Safety properties

- No live lookup is performed.
- The context is explicitly marked `context_hint_only`.
- The preview remains read-only and inert.
- Provider is still bypassed for deterministic preview routing.
- Primary action remains disabled; no write path is connected.

## Verification

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/test_assistant_specialist_agents.py \
  tests/unit/test_assistant_specialist_benchmarks.py \
  tests/unit/test_assistant_specialist_business_diff.py \
  tests/unit/test_assistant_specialist_contract.py \
  tests/unit/test_assistant_specialist_orchestrator.py \
  tests/unit/test_assistant_specialist_preview_renderer.py \
  tests/unit/test_assistant_specialist_preview_surface.py \
  tests/unit/test_assistant_specialist_report.py \
  tests/unit/test_assistant_request_router_integration.py \
  -q
# 64 passed
```

## Non-claims

- This does not yet query production data.
- This does not claim the extracted references are valid records.
- This does not execute or authorize any business action.
