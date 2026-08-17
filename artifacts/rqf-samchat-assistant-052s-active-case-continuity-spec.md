# RQF-SAMCHAT-ASSISTANT-052S - Active Case Continuity Spec

Status: IMPLEMENTED_LOCAL

## Contract

`continuity_context` shape:

```json
{
  "source": "conversation_metadata",
  "lookup_performed": true,
  "authority": "read_only_continuity",
  "matched": true,
  "status": "active_case_found",
  "module_key": "tournaments",
  "module_label": "Torneos",
  "tournament_key": "copa_telmex",
  "active_case": {
    "kind": "tournament_goal_case",
    "case_id": "analyst_case_<hash>",
    "case_version": 3,
    "status": "draft"
  }
}
```

Fail-closed / neutral statuses:

- `no_conversation_metadata`
- `no_active_case`
- `invalid_active_case_pointer`

## Rules

- Read only from the current conversation object already loaded by the assistant turn.
- Do not query unrelated conversations.
- Do not infer ownership beyond the current conversation.
- Do not treat active case presence as approval to execute.
- Invalid pointer data is not rendered as a valid case.

## Integration

- `conversation_service._build_specialist_preview_surface_response` includes `continuity_context`.
- `specialist_live_context.build_specialist_preview_workspace_cards` adds `case_continuity`.
- `assistant_workspace_trace.build_specialist_workspace_step_trace` adds `identify_case_continuity`.
- `assistant_workspace_trace.build_specialist_workspace_source_panel` adds `conversation_continuity`.

## Verification

Focused tests cover:

- active case extraction;
- invalid pointer fail-closed behavior;
- cards/sources/steps include continuity;
- preview remains inert.

## Verification result

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py   tests/unit/test_assistant_specialist_orchestrator.py   tests/unit/test_assistant_specialist_business_diff.py   tests/unit/test_assistant_specialist_preview_renderer.py   tests/unit/test_assistant_specialist_preview_surface.py   tests/unit/test_assistant_specialist_report.py   tests/unit/test_assistant_request_router_integration.py::test_specialist_preview_surface_attaches_read_only_live_context   -q
```

Result: 57 passed.

## Claim

Specialist previews now expose active conversation/case continuity in the operator workspace contract while preserving the read-only preview boundary.
