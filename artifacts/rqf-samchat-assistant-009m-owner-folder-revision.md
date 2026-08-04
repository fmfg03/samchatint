# RQF-SAMCHAT-ASSISTANT-009M Owner Folder Revision Loop

Status: PASS_READONLY_CONVERSATIONAL_REVISION_CONTRACT

## Scope

RQF-009M adds a pure conversational revision layer for owner folder
proposals. It lets the assistant revise an inert folder proposal in response
to user instructions while preserving the approval boundary and read-only
posture.

This stage does not add endpoints, UI, persistence, durable folder creation,
operational table mutations, write handlers, notifications, OCR, webhooks, or
runtime flag activation.

## Files

- src/samchat/assistant/owner_folder_revision.py
- tests/unit/test_assistant_owner_folder_revision.py
- .github/workflows/assistant-scoped-gate.yml
- artifacts/rqf-samchat-assistant-009m-owner-folder-revision.md

## Revision Contract

Every owner folder revision includes:

- revision_id
- base_folder_id
- base_preview_id
- requested_change
- revision_status
- changed_sections
- unchanged_sections
- missing_evidence
- blocked_reason
- approval_required=true
- execution_status=not_executed
- writes_attempted=0
- side_effects_detected=0
- audit_language=folder_revision_proposal_only

## Covered Behaviors

- Finance-oriented revision changes the finance section only.
- Marketing/materiality revision can isolate marketing/materiality.
- Medical/accident revision preserves missing evidence for AI-OWNER-018.
- Write-like requests fail closed as blocked_write_disabled.
- Full owner eval set produces safe revisions.
- Full owner eval set blocks execution-like requests.

## Full Set Result

Normal conversational revision over the 30-prompt owner-needs set produced:

- total=30
- revision_proposed=30
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

Execution-like requested change over the same 30 prompts produced:

- total=30
- blocked_write_disabled=30
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

## CI Gate

The assistant scoped gate now includes:

- tests/unit/test_assistant_owner_folder_revision.py

## Runtime Posture

- ASSISTANT_AGENT_WRITES_ENABLED remains false.
- No general runtime activation is introduced.
- No write authority is introduced.
- No production deployment is performed by this stage.

## Decision

ALLOW_CONVERSATIONAL_PROPOSAL_REVISION_CONTINUE

Still blocked:

- DO_NOT_ENABLE_WRITES
- DO_NOT_ENABLE_GENERAL_RUNTIME
- DO_NOT_EXECUTE_PROPOSED_FOLDERS
- DO_NOT_CLAIM_OPERATIONAL_FOLDER_CREATION
