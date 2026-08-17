# RQF-SAMCHAT-ASSISTANT-052X ? Spec

## Module

`samchat.assistant.operator_workspace_snapshot`

## Contract

`build_operator_workspace_snapshot(...) -> dict`

Inputs:

- `conversation_id`
- `task_id`
- `preview_render`
- `business_preview`
- `understood_context`
- `live_context`
- `continuity_context`
- `memory_context`
- `diagnostics`
- `evidence_quality_gate`
- `resume_guidance`
- `workspace_cards`
- `step_trace`
- `source_panel`

Output:

- `workspace_id`: deterministic hash from conversation/task/preview/schema
- `schema_version`: `operator_workspace_snapshot.v1`
- `persistence_medium`: `assistant_message_tool_payload`
- `status`: `persisted_with_message_payload`
- `authority`: `read_only_workspace_snapshot`
- `operational_writes`: false
- `primary_action_enabled`: false
- `safe_to_execute`: false
- component counts and embedded components

## Helper

`compact_operator_workspace_snapshot(snapshot) -> dict` returns list/recovery metadata without heavy arrays.

## Integration

`conversation_service._build_specialist_preview_surface_response` builds the snapshot after cards/trace/sources, includes it in:

- `tool_trace_entry["specialist_preview_surface"]`
- `tool_trace_entry["result"]`
- persisted assistant `tool_payload`

## Future promotion

A later slice may also save the snapshot as `AssistantArtifact` when the conversation path passes actor identity consistently. This slice intentionally avoids a hidden actor workaround.
