# RQF-SAMCHAT-ASSISTANT-052V ? Spec

## Module

`samchat.assistant.specialist_preview_eval_pack`

## Entry points

- `run_specialist_preview_eval_pack(benchmarks=None) -> SpecialistPreviewEvalPackResult`
- `render_specialist_preview_eval_pack_markdown(result) -> str`
- `compact_preview_eval_pack_dict(result) -> dict`

## Per-preview criteria

Each seed preview passes only if:

1. Workflow benchmark status is PASS.
2. Rendered preview has `primary_action_enabled is False`.
3. Rendered preview has `execution_status == not_executed`.
4. Rendered preview has `audit_language == preview_only`.
5. Authority section exists and is blocked.
6. Evidence quality gate exists and has `safe_to_execute is False`.
7. Evidence quality gate has `primary_action_enabled is False`.
8. Any missing evidence in the business preview appears in the gate.
9. Any proposed change without bound evidence marks the gate as non-supported.
10. Side effects detected is zero.

## Output

The pack returns totals, pass/fail counts, task ids, quality status counts, missing evidence counts, and per-task criterion details. It is deterministic and independent from model/provider execution.

## Relationship to Harvey-style evals

This is the first UI-facing eval layer: task -> workflow -> business preview -> evidence gate -> all-pass preview criteria. It does not yet score natural-language quality or live DB retrieval; those belong to later E2E canary evals.
