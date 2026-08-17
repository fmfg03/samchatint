# RQF-SAMCHAT-ASSISTANT-052U ? Spec

## Contract

`build_specialist_evidence_quality_gate(...)` returns a plain dictionary suitable for UI cards and assistant traces.

Required inputs:

- `business_preview`: source of proposed changes, found evidence, and missing evidence.
- `live_context`: read-only DB lookup status and unresolved references.
- `diagnostics`: deterministic operational readiness findings.
- `memory_context`: optional precedent context.
- `continuity_context`: optional active-case context.

## Output fields

- `source`: `deterministic_specialist_evidence_quality_gate`
- `authority`: `read_only_evidence_gate`
- `quality_status`: one of `supported`, `partial`, `insufficient`
- `safe_to_continue_preview`: boolean
- `safe_to_execute`: always `False` in this slice
- `primary_action_enabled`: always `False`
- `supported_change_count`
- `unbound_change_count`
- `found_evidence_count`
- `missing_evidence_count`
- `precedent_count`
- `current_case_matched`
- `execution_blockers`
- `caveats`
- `next_steps`
- `writes_attempted`: `False`

## Rules

1. A proposed change is supported only when it has a non-empty `evidence_id`.
2. Proposed changes without evidence are unbound and must be called out.
3. Missing evidence from `business_preview.missing_evidence`, diagnostics missing items, or unresolved live references prevents execution readiness.
4. Memory snippets can improve orientation but are `precedent_only`; they do not satisfy current-case evidence requirements.
5. Continuity metadata identifies the active case but does not authorize any business fact.
6. The gate may allow continuing a read-only preview if there is at least some support and no hard lookup error, but it never enables execution.

## Integration points

- Assistant message: render a short `Calidad de evidencia` section before the business preview.
- Workspace cards: add `evidence_quality` after operational diagnostics.
- Step trace: add `gate_evidence_quality` before preview preparation.
- Source panel: add `deterministic_evidence_quality_gate` before the preview contract.
- Tool trace and persisted payload: include `evidence_quality_gate`.

## Tests

- Unit tests for supported, partial, and insufficient quality states.
- Markdown rendering test.
- Workspace cards include the gate when provided.
- Conversation preview payload persists the gate.
