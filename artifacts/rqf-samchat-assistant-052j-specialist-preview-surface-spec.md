# RQF-SAMCHAT-ASSISTANT-052J - Specialist Preview Surface Spec

Status: DRAFT_SPEC
Depends on: RQF-052A, RQF-052B, RQF-052I
Implementation scope: deterministic read-only assistant surface

## Existing foundation

The stage builds on the current modules:

- `samchat.assistant.specialist_contract`
- `samchat.assistant.samchat_task_schema`
- `samchat.assistant.operational_case`
- `samchat.assistant.specialist_agents`
- `samchat.assistant.specialist_orchestrator`
- `samchat.assistant.specialist_business_diff`
- `samchat.assistant.specialist_benchmarks`
- `samchat.assistant.specialist_report`

The surface must not bypass these modules. It should consume benchmark/business
preview outputs rather than directly reading private rubrics or domain services.

## New/confirmed modules

### `specialist_preview_renderer.py`

Responsibility: convert `SpecialistBusinessDiffPreview` into a UI/assistant
render contract.

Required output fields:

- `title`
- `summary`
- `sections[]`
- `primary_action_label`
- `primary_action_enabled=false`
- `execution_status=not_executed`
- `authority_boundary=human_approval_required`
- `audit_language`

Renderer rules:

- Show proposed changes only when supported by verifier.
- Show missing evidence separately.
- Never word a preview as executed.
- Avoid accounting/payment verbs that imply posting or transfer.

### `specialist_preview_surface.py`

Responsibility: assistant-facing deterministic surface.

Inputs:

- raw user message;
- optional explicit `task_id`;
- optional context hints extracted from message.

Outputs:

- `SpecialistPreviewSurface`; or
- fail-closed unsupported result.

Routing rules:

1. If an explicit seed `task_id` is present, use it if known.
2. Else normalize user text and match only against allowlisted natural-language
   rules.
3. A natural-language rule requires:
   - at least one domain term;
   - required minimum supporting signals;
   - no competing stronger route.
4. If ambiguous, return unsupported/needs clarification.

## Seed task coverage

The stage may route only existing seed benchmarks, including but not limited to:

- CxC / collection preview;
- AMEX reconciliation preview;
- supplier/hotel/ISH preview;
- team registration preview;
- player eligibility preview;
- document incident preview;
- money request/reimbursement preview;
- budget preview;
- tournament setup preview;
- owner/entity folder preview.

## Live context boundary

This stage may extract lightweight context hints from text, such as:

- document refs: `S-26000071`, `I-991520`, `O-26000312`;
- operations refs: `ref 28`, `referencia 9`;
- UUID prefixes;
- account codes.

It must not require live DB access to pass. Live context enrichment is a later
stage unless already implemented as read-only, fail-closed enrichment.

## Security and authority invariants

- No provider call is required.
- No database write is permitted.
- No domain action is executed.
- Private rubric remains evaluator-only.
- Unsupported requests return no proposal.
- Preview primary action is disabled.
- Tool trace records no writes and no provider call.

## Suggested tests

- `test_explicit_task_id_renders_preview`
- `test_natural_language_amex_routes_to_seed_preview`
- `test_unsupported_request_fails_closed`
- `test_ambiguous_request_does_not_guess_route`
- `test_preview_renderer_disables_primary_action`
- `test_preview_contains_supported_changes_and_missing_evidence`
- `test_surface_tool_trace_declares_no_provider_and_no_writes`
- Regression: specialist contract, agents, orchestrator, business diff and report
  tests remain green.

## Out of scope

- Building the production UI card.
- Opening approval receipts.
- Live execution or writes.
- Adding new specialist domains beyond seed tasks.
- LLM-based route selection.

## Next stages

- 052K: live-context grounding for selected references, fail-closed and read-only.
- 052L: UI card / assistant workspace integration.
- 052M: approval receipt boundary design, still without production writes.
