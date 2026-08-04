# RQF-SAMCHAT-ASSISTANT-009L Owner Folder Builder

Status: PASS_READONLY_OWNER_FOLDER_PROPOSAL_CONTRACT

## Scope

RQF-009L adds a pure read-only owner folder proposal builder on top of
the approved owner-needs assessment and business diff preview contracts.

The builder does not create durable business folders, mutate operational
tables, call write handlers, send notifications, run OCR, call webhooks, or
alter assistant runtime flags.

## Files

- src/samchat/assistant/owner_folder_builder.py
- tests/unit/test_assistant_owner_folder_builder.py
- .github/workflows/assistant-scoped-gate.yml
- artifacts/rqf-samchat-assistant-009l-owner-folder-builder.md

## Proposal Contract

Every owner folder proposal includes:

- folder_id
- folder_type
- target
- sections
- evidence_summary
- missing_evidence
- preview_id
- approval_required=true
- execution_status=not_executed
- writes_attempted=0
- side_effects_detected=0
- audit_language=folder_proposal_only

Every proposed section field includes:

- field
- label
- value
- source
- status: supported or missing_evidence
- confidence
- reason

## Covered Folder Types

- entity_folder_proposal
- national_phase_folder_proposal
- activation_report_proposal
- folder_build_plan_proposal

## Owner Prompt Coverage

- AI-OWNER-001: entity folder proposal for Jalisco / beisbol.
- AI-OWNER-013: national phase folder proposal.
- AI-OWNER-025: activation report proposal.
- AI-OWNER-028: entity update remains proposal-only.
- AI-OWNER-018: medical and accident evidence remains explicit missing evidence.

## Full Set Result

The 30-prompt owner-needs evaluation set produced:

- total=30
- entity_folder_proposal=2
- folder_build_plan_proposal=25
- national_phase_folder_proposal=2
- activation_report_proposal=1
- writes_attempted=0
- side_effects_detected=0
- execution_claims_detected=0

## CI Gate

The assistant scoped gate now includes:

- tests/unit/test_assistant_owner_folder_builder.py

## Runtime Posture

- ASSISTANT_AGENT_WRITES_ENABLED remains false.
- No general runtime activation is introduced.
- No write authority is introduced.
- No production deployment is performed by this stage.

## Decision

ALLOW_BUSINESS_FOLDER_PROPOSAL_PREVIEW_CONTINUE

Still blocked:

- DO_NOT_ENABLE_WRITES
- DO_NOT_ENABLE_GENERAL_RUNTIME
- DO_NOT_EXECUTE_PROPOSED_FOLDERS
- DO_NOT_CLAIM_OPERATIONAL_FOLDER_CREATION
