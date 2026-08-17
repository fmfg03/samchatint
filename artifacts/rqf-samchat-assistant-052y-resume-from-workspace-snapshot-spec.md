# Spec ? Resume from workspace snapshot

## Contract

`operator_workspace_resume` is a deterministic read-only assistant path. It consumes a previously persisted `operator_workspace_snapshot.v1` from assistant message `tool_payload` and renders a compact continuation surface.

## Inputs

- `raw_message`: user text.
- `conversation.id`: scope for message history lookup.
- `AssistantMessage.tool_payload.operator_workspace_snapshot`: persisted v0 workspace source of truth.

## Validation

A snapshot is resumable only if:

- `schema_version == operator_workspace_snapshot.v1`;
- `authority == read_only_workspace_snapshot`;
- `workspace_id` is present;
- `primary_action_enabled == false`;
- `safe_to_execute == false`.

## Output

The response includes:

- workspace id;
- task id;
- preview id;
- evidence quality;
- readiness;
- resume recommendation;
- compact component counts;
- read-only authority boundary.

## Non-goals

- No `AssistantArtifact` write yet.
- No execution of business actions.
- No provider/LLM fallback.
- No synthetic actor attribution.
- No cross-conversation lookup.

## Tests

- Explicit resume intent detection.
- Generic continuation text is not hijacked.
- Invalid/unsafe snapshot is rejected.
- Latest valid snapshot is loaded and compacted.
- Conversation path bypasses provider and persists resume payload.
