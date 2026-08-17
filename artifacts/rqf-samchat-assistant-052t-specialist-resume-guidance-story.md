# RQF-SAMCHAT-ASSISTANT-052T - Specialist Resume Guidance Story

Status: IMPLEMENTED_LOCAL

## User story

As a SamChat user looking at a specialist preview, I want the assistant to tell me the safest next step from the current case context, so I can resume work without confusing read-only context, precedent, preview and authorization.

## Product intent

After 052R and 052S, the assistant can show historical memory and active case continuity. This slice makes that visible context operationally useful: it produces a deterministic recommendation for what to do next, while keeping execution blocked.

## Scope

- Build a read-only `resume_guidance` bundle from diagnostics, continuity and memory.
- Attach it to specialist preview messages and payloads.
- Show it as card/source/step in the operator workspace contract.
- Preserve the rule that guidance is not authority.

## Out of scope

- Running the recommended action.
- Creating approval receipts.
- Mutating active cases.
- Cross-conversation resume.

## Acceptance criteria

- If diagnostics need more context, guidance recommends collecting missing references first.
- If an active case exists and diagnostics are ready, guidance recommends continuing the active preview/diff path.
- If only memory exists, guidance labels it as precedent-only.
- Guidance never enables `primary_action_enabled`.
- Tests cover active, missing-context and precedent-only paths.

## Closeout

Implemented locally in branch `codex/rqf-assistant-artifact-audit-001`.

Result:

- Specialist previews now include deterministic resume guidance.
- Guidance distinguishes missing context, active-case continuation, precedent-only review, and isolated preview.
- Guidance is visible in message, payload, cards, step trace and source panel.
- Authority remains blocked; guidance informs but does not execute.
