# RQF-SAMCHAT-ASSISTANT-052R - Specialist Case Memory Grounding Story

Status: IMPLEMENTED_LOCAL

## User story

As a SamChat user asking for a specialist preview, I want the assistant to surface relevant prior case memory when available, so the preview can be informed by how Plataforma Sports handled similar cases before, without treating that precedent as permission to act.

## Product intent

This slice closes the gap between:

- the existing specialist preview surface, and
- the existing deterministic case memory artifacts.

The assistant should not behave as if every specialist request starts from zero. It should show compact prior-case snippets when they match the current request, while preserving the rule:

> Precedent informs; it never authorizes.

## Scope

- Resolve persisted `case_memory_summary` artifacts for the current user's assistant scope.
- Attach memory context to specialist preview responses.
- Show memory as a read-only workspace card/source.
- Keep provider calls, writes, approvals and action execution disabled.

## Out of scope

- Creating new memories automatically.
- Cross-user memory sharing.
- LLM summarization of memory.
- Action execution from memory.
- Frontend redesign beyond using the existing card/source payload contracts.

## Acceptance criteria

- If no DB session exists, the memory lookup fails closed.
- If no employee scope exists, the memory lookup fails closed.
- If no memory matches, the preview still works normally.
- If memory matches, the assistant message and payload include read-only memory snippets.
- Workspace cards and source panel expose memory separately from live DB evidence.
- Memory snippets cannot enable `primary_action_enabled`.
- Tests prove the preview remains inert.

## Closeout

Implemented locally in branch `codex/rqf-assistant-artifact-audit-001`.

Result:

- Specialist previews now include deterministic read-only case memory context when matching summaries exist.
- Memory appears as its own workspace card/source/step.
- Preview execution remains inert and authority-blocked.
