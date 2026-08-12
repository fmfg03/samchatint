# RQF-SAMCHAT-ASSISTANT-052F - Specialist Orchestrator Routing

Status: CLOSED_COMMITTED_PENDING_REMOTE_CI

## Objective

Make the first SamChat specialist composition explicit and testable. The prior
seed harness already executed Knowledge -> Verifier -> Finance, but the route
was hidden inside the benchmark helper. This slice introduces an orchestrator
surface that records the route, ordered steps, produced artifacts, authority
boundary, and side-effect count.

## Implemented

- Added `specialist_orchestrator.py` with a phase-one route:
  `verified_finance_preview_v0`.
- The orchestrator runs the canonical handoff:
  1. `institutional_knowledge_v0` -> `precedent_pack`
  2. `evidence_verifier_v0` -> `verification_report`
  3. `finance_v0` -> `finance_proposal`
- Seed benchmarks now execute through the orchestrator while preserving the
  existing `workflow` result shape.
- Benchmark output includes an `orchestrator` block with route, status, steps,
  authority boundary, execution flag, and side effects.
- Unsupported routes fail closed instead of silently running a best-effort path.
- Assistant scoped gate now includes orchestrator tests.

## Safety properties

- The orchestrator receives only `SamchatVisibleTask`; private rubric/criteria
  remain unavailable to solver code.
- Route selection is based on visible expected artifacts, not evaluator ground
  truth.
- Execution remains disabled: `execution_allowed = false` and
  `authority_boundary = human_approval_required`.
- The new trace is observational only; no production workflows or writes are
  connected.

## Local verification

- `tests/unit/test_assistant_specialist_orchestrator.py`
- Specialist benchmark block including contract, agents, benchmarks, report,
  and orchestrator tests.
- Compile and diff hygiene checks.

## Non-claims

- This is not a production assistant runtime.
- This does not enable real writes.
- This does not yet implement domain-specific specialist capabilities beyond
  the existing deterministic finance preview route.
