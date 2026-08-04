# RQF-SAMCHAT-ASSISTANT-009F - Business Diff Preview Pattern

Status: BUSINESS_DIFF_PREVIEW_CONTRACT_READY
Date: 2026-08-04
Scope: read-only owner-needs preview/diff contract

## Objective

Define a reusable, deterministic business diff preview pattern for owner-needs
requests before any durable folder, report, update, publication, or write-capable
action.

This stage does not expose a new endpoint, does not change UI, and does not
execute writes.

## Material Changes

- Added `src/samchat/assistant/business_diff_preview.py`.
- Added `tests/unit/test_assistant_business_diff_preview.py`.
- Updated `.github/workflows/assistant-scoped-gate.yml` to include the new test.

## Contract

Each preview includes:

- `preview_id`
- `operation_type`
- `target`
- `found_evidence`
- `missing_evidence`
- `proposed_changes`
- `blocked_reason`
- `approval_required`
- `execution_status`
- `writes_attempted`
- `side_effects_detected`
- `audit_language`

Each proposed change includes:

- `field`
- `proposed_value`
- `source`
- `confidence`
- `reason`
- `status`

## Operation Types Covered

| Operation | Purpose |
| --- | --- |
| `create_entity_folder` | Preview creation of one entity folder. |
| `create_national_phase_folder` | Preview creation of one national-phase folder. |
| `generate_activation_report` | Preview a brand activation report. |
| `update_entity_folder` | Preview updates to one entity folder. |
| `plan_folder_build` | Preview read-only planning when no create/update/report action is requested. |

## Full Eval Preview Summary

The 30 owner-needs prompts were converted into previews.

| Metric | Value |
| --- | ---: |
| Prompts previewed | 30 |
| `create_entity_folder` | 1 |
| `create_national_phase_folder` | 2 |
| `generate_activation_report` | 1 |
| `update_entity_folder` | 1 |
| `plan_folder_build` | 25 |
| Writes attempted | 0 |
| Side effects detected | 0 |
| Execution claims detected | 0 |

## Required Safety Behavior

For create/update/report prompts such as `AI-OWNER-001`, `AI-OWNER-013`,
`AI-OWNER-025`, and `AI-OWNER-028`, the preview must say:

- `approval_required=true`
- `execution_status=not_executed`
- `blocked_reason=approval_required`
- `writes_attempted=0`
- `side_effects_detected=0`
- `audit_language=preview_only`

The preview can describe what would be created or updated, but it must not claim
that it was created, updated, generated, published, sent, or executed.

## AI-OWNER-018 Medical Evidence Boundary

The medical-services prompt now produces explicit missing fields:

- `medical_services_description`
- `accidents_with_transfers`
- `medical_and_insurance_costs`

These fields remain `missing_evidence` unless live document,
medical/event_incident, finance, provider, or insurance evidence is supplied.

## Validation

Focused tests:

```bash
PYTHONPATH=src:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=/tmp/samchat-009f-pycache /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest -p no:cacheprovider -o addopts='' tests/unit/test_assistant_business_diff_preview.py tests/unit/test_assistant_owner_needs_eval.py -q
```

Result:

- 12 passed

## Claim Boundary

Established:

- deterministic owner-needs prompts can be converted into read-only previews;
- missing evidence is represented as missing evidence, not invented facts;
- create/update/report requests stop at approval-required preview;
- no writes or side effects are represented in the contract;
- the assistant scoped gate includes the preview test.

Not established:

- endpoint or UI rendering of previews;
- durable folder creation;
- approval receipts bound to preview IDs;
- execution after approval;
- live evidence wiring for every field.

## Next Stage

Recommended next stage:

`RQF-ASSISTANT-009L - Evidence-backed Owner Folder Builder Contract`

Alternative if UI is prioritized:

`RQF-ASSISTANT-010 - Preview-to-Approval UI/Trace Shell`
