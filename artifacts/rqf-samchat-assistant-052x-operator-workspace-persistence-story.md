# RQF-SAMCHAT-ASSISTANT-052X ? Assistant operator workspace persistence

## Story

As a SamChat user, when the assistant prepares a specialist preview, the workspace state should be durable and resumable instead of existing only as prose in the latest assistant message.

## Problem

Specialist previews already build workspace cards, step trace, source panel, diagnostics, evidence quality, and resume guidance. Those structures are persisted inside the message payload, but there is no explicit workspace snapshot contract with a stable id, schema version, and rehydration boundary. That makes the next UI/operator layer harder to build and test.

## Goal

Create a deterministic operator workspace snapshot for specialist previews and persist it in the assistant message tool payload and tool trace.

## Non-goals

- No new database table.
- No separate `AssistantArtifact` write yet, because the preview helper does not consistently receive an actor id in direct usage.
- No frontend UI change.
- No operational writes.
- No approval receipt.

## Acceptance criteria

- Every specialist preview response includes an `operator_workspace_snapshot` payload.
- Snapshot has stable `workspace_id`, `schema_version`, `task_id`, `preview_id`, cards, step trace, source panel, evidence quality, resume guidance, and authority boundary.
- Snapshot declares `persistence_medium=assistant_message_tool_payload`.
- Snapshot declares `operational_writes=false` and `primary_action_enabled=false`.
- Snapshot can be compacted for UI list/recovery.
- Tests prove snapshot shape, stable id, persistence in response payload, and no execution authority.
