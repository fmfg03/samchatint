# RQF-SAMCHAT-ASSISTANT-009O Owner Operator Workflow

Status: PASS_READONLY_OWNER_OPERATOR_WORKFLOW_CONTRACT

## Scope

RQF-009O adds a pure read-only workflow orchestrator over the owner-needs
assistant pipeline:

owner prompt -> evidence-gap assessment -> business diff preview -> folder
proposal -> optional revision -> operator response pack

This stage does not add endpoints, UI, persistence, durable folder creation,
operational table mutations, write handlers, notifications, OCR, webhooks, or
runtime flag activation.

## Files

- src/samchat/assistant/owner_operator_workflow.py
- tests/unit/test_assistant_owner_operator_workflow.py
- .github/workflows/assistant-scoped-gate.yml
- artifacts/rqf-samchat-assistant-009o-owner-operator-workflow.md

## Workflow Contract

Every workflow result includes:

- workflow_id
- prompt_id
- assessment
- preview
- folder_proposal
- revision
- response_pack
- trace
- safety_summary
- execution_status=not_executed
- writes_attempted=0
- side_effects_detected=0
- audit_language=owner_operator_workflow_only

## Trace Contract

Trace includes:

- assessment_status
- preview_id
- folder_id
- response_id
- revision_id when a revision is requested
- revision_status when a revision is requested

## Safety Summary

Safety summary includes:

- approval_required=true
- writes_enabled=false
- write_handlers_invoked=0
- side_effects_detected=0
- runtime_general_enabled=false
- writes_attempted=0

## Covered Behaviors

- AI-OWNER-001 without revision responds from proposal.
- AI-OWNER-001 with normal revision responds from revision.
- AI-OWNER-028 with write-like revision fails closed as blocked_write_disabled.
- AI-OWNER-018 preserves missing medical/event evidence in preview, proposal,
  and response pack.
- Full owner eval set produces safe workflow results across proposal-only,
  normal revision, and write-like revision paths.

## Full Set Result

No revision over the 30-prompt owner-needs set produced:

- total=30
- blocked_write_disabled=0
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

Normal revision over the 30-prompt owner-needs set produced:

- total=30
- blocked_write_disabled=0
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

Write-like revision over the 30-prompt owner-needs set produced:

- total=30
- blocked_write_disabled=30
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

## CI Gate

The assistant scoped gate now includes:

- tests/unit/test_assistant_owner_operator_workflow.py

## Runtime Posture

- ASSISTANT_AGENT_WRITES_ENABLED remains false.
- No general runtime activation is introduced.
- No write authority is introduced.
- No production deployment is performed by this stage.

## Decision

ALLOW_OWNER_OPERATOR_WORKFLOW_CONTINUE

Still blocked:

- DO_NOT_ENABLE_WRITES
- DO_NOT_ENABLE_GENERAL_RUNTIME
- DO_NOT_EXECUTE_PROPOSED_FOLDERS
- DO_NOT_CLAIM_OPERATIONAL_FOLDER_CREATION
