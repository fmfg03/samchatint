# RQF-SAMCHAT-ASSISTANT-052I - Specialist Business Diff Preview

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Expose specialist-agent output as a user-reviewable business diff. The assistant
should be able to show what it proposes to prepare, what evidence supports it,
what is still missing, and why execution remains blocked until human approval.

## Implemented

- Added `specialist_business_diff.py`.
- Added `SpecialistBusinessDiffPreview` and `SpecialistBusinessChange`.
- Seed benchmark results now include `business_preview` alongside workflow and
  orchestrator metadata.
- Business previews include:
  - `preview_type`
  - target metadata
  - verified proposed changes
  - found evidence ids
  - missing evidence references
  - domain action steps and checks
  - execution/approval guard fields
- Benchmark report now summarizes business preview types and missing evidence
  count.

## Safety properties

- Proposed changes are created only from verifier-supported claims.
- Missing evidence remains explicit and does not become a proposed value.
- Every preview is inert: no writes, no execution claim, human approval required.

## Non-claims

- This does not yet render the preview in the production UI.
- This does not create approval receipts.
- This does not execute accounting, payment, budget, tournament, or document
  mutations.
