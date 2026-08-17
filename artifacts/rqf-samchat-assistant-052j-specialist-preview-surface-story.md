# RQF-SAMCHAT-ASSISTANT-052J - Specialist Preview Surface Story

Status: DRAFT_STORY
Owner track: SamChat Assistant / Specialist Agents
Factory phase: Story before implementation/spec lock

## Objective

Turn the existing specialist-agent benchmark artifacts into an assistant-facing,
read-only preview surface that a user can understand before any production write
path exists.

The assistant should be able to recognize a narrow set of real SamChat business
requests, route them to a deterministic specialist benchmark, and show a
proposal-style preview containing: what would be prepared, what evidence backs
it, what is missing, and why execution remains blocked.

## Product claim

SamChat can present specialist-agent work as a bounded, reviewable business
preview for selected seed scenarios, without calling external providers and
without executing operational changes.

## Non-claims

This story does not claim:

- autonomous execution;
- production accounting, payment, budget, tournament, roster, or document writes;
- broad natural-language routing;
- full live retrieval across all SamChat data;
- human approval receipts;
- replacement of domain workflows;
- Claude Code parity for operations.

## Why this matters

The specialist-agent foundation already has pieces that are valuable but mostly
invisible: operational cases, visible tasks, private rubrics, Knowledge ->
Verifier -> Finance handoff, orchestrator trace and business diff previews.

Without a preview surface, the user only sees test machinery. With this story,
SamChat starts showing the product behavior we want: "I inspected a bounded
case, here is the supported proposal, here is the missing evidence, and here is
why I cannot execute yet."

## User story

As a SamChat user, when I ask for help with a known business scenario such as
AMEX reconciliation, CxC, budget, tournament setup, team registration or a
document incident, I want the assistant to show a clear specialist preview so I
can review the proposed work and missing evidence before any action is taken.

## Representative prompts

- "Prepara una vista previa para la comprobacion AMEX de la referencia 28."
- "Muestrame que propondria el especialista para ligar la factura DCC a CxC."
- "Revisa este caso de hospedaje con ISH y dime que evidencia falta."
- "Prepara el caso de crear Copa Telmex 2027 con Sub-17."
- "Dame un preview especialista para una duplicidad de CURP."

## Acceptance criteria

1. The surface accepts explicit task ids from the seed benchmark set.
2. The surface can route a small allowlist of natural-language requests to seed
   tasks only when there are strong domain signals.
3. Unsupported or ambiguous requests fail closed with no preview and no side
   effects.
4. The rendered preview includes:
   - task id;
   - business preview type;
   - proposed changes from verified claims only;
   - evidence ids found;
   - missing evidence;
   - required human authority;
   - disabled execution action.
5. The surface never exposes private rubric criteria to the solver/runtime.
6. The surface never enables writes, even when the proposal is complete.
7. The assistant trace reports provider_called=false and writes_attempted=false.
8. The benchmark report can summarize preview types and missing evidence.
9. The implementation remains deterministic and unit-testable.
10. Existing specialist contract, handoff, orchestrator and report tests remain green.

## Quality bar

A PASS means the preview is useful, bounded and honest. It does not mean the
assistant can yet perform the task. The next stage after this story should be
live-context grounding and UI integration, not writes.

## Evidence to close

- Unit tests for explicit task id routing.
- Unit tests for natural-language routing.
- Unit tests for ambiguous/unsupported fail-closed behavior.
- Unit tests for rendered preview contents and disabled action state.
- Focused assistant/specialist test run.
- Commit hash.

## Closure statement template

RQF-SAMCHAT-ASSISTANT-052J is closed when SamChat can render deterministic,
read-only specialist previews for selected seed scenarios, with evidence and
missing-evidence boundaries visible, and with execution still blocked pending a
future human approval receipt stage.
