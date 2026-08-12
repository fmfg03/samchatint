# RQF-SAMCHAT-ASSISTANT-052H - Domain Action Previews

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Move from domain labels to domain-shaped work previews. Each finance capability
now produces a small, deterministic `action_preview` that explains what business
workflow SamChat would prepare, while remaining completely inert.

## Implemented

- Added `finance_action_preview(domain_summary)`.
- Added `action_preview` to `finance_proposal`.
- Preview fields are uniform:
  - `preview_type`
  - `steps`
  - `checks`
  - `execution_allowed = false`
  - `requires_human_approval = true`
  - `authority_boundary = human_approval_required`
- Added domain-shaped steps for:
  - AMEX reconciliation
  - CxC / collection
  - budget preview
  - money request / reimbursement
  - tournament financial context
  - operations context
  - supplier precedent
  - general finance preview

## Safety properties

- Action previews are generated only after Knowledge -> Verifier -> Finance.
- Preview checks are derived from supported verified claims via `domain_summary`.
- No real action is executed or authorized.

## Non-claims

- This is not yet a real payment, accounting, budget, or tournament executor.
- This does not create a human approval receipt.
- This does not yet render the preview in the production assistant UI.
