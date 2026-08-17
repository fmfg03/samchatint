# RQF-SAMCHAT-ASSISTANT-052U ? Preview evidence quality gate

## Story

As SamChat prepares specialist previews, the user must see whether the proposal is backed by current case evidence, only informed by precedent, or still missing required support before any human approval boundary is even considered.

## Problem

The specialist preview can already render a business diff, live context, case memory, diagnostics, and resume guidance. However, those surfaces can be visually convincing even when the evidence posture is mixed: supported changes, missing evidence, precedent-only guidance, or unresolved live references. That creates a product risk: users may read a preview as more certain than it is.

## Goal

Add a deterministic, read-only evidence quality gate to specialist previews that classifies evidence as supported, missing, precedent-only, or unbound/inferred, and exposes the result in the assistant response, workspace cards, step trace, source panel, persisted payload, and tool trace.

## Non-goals

- No provider calls.
- No writes.
- No approval receipts.
- No production workflow execution.
- No attempt to make precedent or memory authoritative.

## Acceptance criteria

- The gate consumes the specialist business preview and contextual surfaces without mutating them.
- Supported changes require explicit `evidence_id` values in proposed changes.
- Missing evidence from the business preview and diagnostics is surfaced as blocking for execution.
- Case memory is labelled as precedent context, never as proof for the current transaction.
- The primary action remains disabled.
- The gate is present in persisted assistant payloads and tool traces.
- Focused tests cover supported, partial, and insufficient evidence postures.
