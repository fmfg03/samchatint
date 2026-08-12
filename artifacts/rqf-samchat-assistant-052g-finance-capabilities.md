# RQF-SAMCHAT-ASSISTANT-052G - Domain-Specific Finance Capabilities

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Stop treating every verified finance proposal as a generic amount/supplier/account
preview. Keep the deterministic v0 agent small, but make its output say what
kind of business action it is preparing: AMEX reconciliation, CxC/collection,
budget preview, money request, tournament context, operations context, or
supplier precedent.

## Implemented

- Added visible-task-only finance capability classification.
- Added `finance_capability` and `domain_summary` to `finance_proposal`.
- Domain summaries remain preview-only and are built only from supported verified
  claims.
- Covered current seed scenarios:
  - AMEX expense reconciliation
  - CxC collection
  - budget preview
  - money request preview
  - tournament financial context
  - operations context
  - supplier financial precedent
- Added regression tests that verify the expected capability and selected
  domain fields for the 10 seed benchmarks.

## Safety properties

- Capability selection uses only `SamchatVisibleTask.tags` and `case_type`; it
  does not read rubric criteria or expected answers.
- Domain summaries consume only verifier-supported claims.
- No writes are enabled; `execution_allowed` remains false and authority remains
  `human_approval_required`.

## Non-claims

- This does not yet split Finance into separate runtime classes.
- This does not yet generate full accounting entries.
- This is still a deterministic benchmark/runtime foundation, not production
  autonomous execution.
