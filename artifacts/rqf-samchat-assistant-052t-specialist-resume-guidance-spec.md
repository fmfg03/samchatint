# RQF-SAMCHAT-ASSISTANT-052T - Specialist Resume Guidance Spec

Status: IMPLEMENTED_LOCAL

## Contract

`resume_guidance` shape:

```json
{
  "source": "deterministic_resume_guidance",
  "authority": "read_only_guidance",
  "status": "ready_to_continue_active_case",
  "recommended_mode": "continue_preview",
  "recommendation": "Continuar con preview/diff read-only del caso activo.",
  "blocked_until": ["human approval", "idempotency key", "audit trail"],
  "uses_active_case": true,
  "uses_case_memory": true,
  "writes_attempted": false
}
```

Statuses:

- `needs_more_context`
- `ready_to_continue_active_case`
- `ready_for_isolated_preview`
- `precedent_only`

## Rules

- Guidance is deterministic and read-only.
- Guidance consumes only already-built contexts; it does not query external sources.
- Guidance may recommend a next human-reviewed step but cannot enable execution.
- Case memory is labelled as precedent, never as authority.

## Integration

- `conversation_service._build_specialist_preview_surface_response` includes `resume_guidance`.
- Workspace cards include `resume_guidance`.
- Step trace includes `recommend_safe_next_step`.
- Source panel includes `deterministic_resume_guidance`.

## Verification

Focused specialist tests must remain green and assert:

- guidance is in message/payload;
- guidance status changes with diagnostics/continuity/memory;
- authority remains blocked.

## Verification result

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py   tests/unit/test_assistant_specialist_orchestrator.py   tests/unit/test_assistant_specialist_business_diff.py   tests/unit/test_assistant_specialist_preview_renderer.py   tests/unit/test_assistant_specialist_preview_surface.py   tests/unit/test_assistant_specialist_report.py   tests/unit/test_assistant_request_router_integration.py::test_specialist_preview_surface_attaches_read_only_live_context   -q
```

Result: 59 passed.

## Claim

Specialist previews now produce deterministic read-only resume guidance from diagnostics, active continuity and case memory while preserving the authority boundary.
