# Spec ? Operator continuity surface

## Input

A valid `operator_workspace_snapshot.v1` previously persisted in the assistant message payload.

## Derived surface

`build_operator_workspace_continuity_surface(snapshot)` derives:

- `what_i_know`: references, UUID prefixes, domains, entities and live context counts.
- `findings`: deterministic diagnostic findings.
- `missing`: missing context/evidence before advancing.
- `risks`: diagnostic risks.
- `next_steps`: resume recommendation and diagnostic next steps.
- `available_sources`: source panel titles.
- `available_steps`: step trace titles.
- `available_cards`: workspace card titles/ids.
- `recommended_preview_task_id`: original specialist task id.

## Authority boundary

The surface is explicitly read-only:

- `provider_called=false`
- `writes_attempted=false`
- `primary_action_enabled=false`
- `safe_to_execute=false`

## Rendering

`render_operator_workspace_resume_markdown` includes the continuity surface in human-readable form under the resumed workspace response.

## Non-goals

- No new storage table.
- No cross-conversation lookup.
- No action execution.
- No approval receipt generation.
