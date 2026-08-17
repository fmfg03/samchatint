# RQF-SAMCHAT-ASSISTANT-052V ? Specialist preview eval pack

## Story

As SamChat evolves toward a Claude-Code-like operator, every specialist preview must be evaluated as a product artifact, not only as an internal workflow result.

## Problem

The seed benchmark harness already verifies that specialist workflows produce expected proposals without executing writes. The new preview surface now also includes live context, memory, continuity, diagnostics, evidence quality, workspace cards, step trace, and source panel. Without a focused eval pack, regressions in those user-facing safety surfaces could pass the older workflow-only benchmark.

## Goal

Create a deterministic preview eval pack that runs the existing 10 specialist seed benchmarks through the business preview renderer and evidence quality gate, then scores each preview with all-pass criteria.

## Non-goals

- No new business workflow.
- No provider/model eval.
- No live database dependency.
- No writes, approvals, or execution receipts.
- No replacement of the seed benchmark harness.

## Acceptance criteria

- All 10 seed benchmarks produce preview eval results.
- Every preview must keep `primary_action_enabled=false` and `execution_status=not_executed`.
- Every preview must have authority section blocked.
- Every preview must have an evidence quality gate.
- Missing evidence is allowed only when explicitly surfaced by the gate and preview.
- Memory/precedent context is never treated as execution authority.
- A compact report is available for CI/readout.
