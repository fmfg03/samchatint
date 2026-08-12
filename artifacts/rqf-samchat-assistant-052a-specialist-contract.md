# RQF-ASSISTANT-052A ? Specialist Agent Contract + Case/Task Schema

Status: IMPLEMENTED_LOCAL

## Scope

This slice establishes the first non-production foundation for the Harvey-style
SamChat specialist-agent architecture:

- common phase-one specialist agent contracts;
- agent-visible `samchat_task` payload separated from private rubric;
- `operational_case` schema for tournament/team/finance/supplier style cases;
- fail-closed evidence binding for case facts;
- minimal read-only benchmark harness with all-pass semantics;
- assistant scoped gate coverage for the new invariant tests.

## Explicit non-claims

This does not yet implement the full SamChat Orchestrator, live retrieval,
10 production benchmarks, specialist LLM tools, or write execution. It is the
contractual runway for those pieces.

## Safety invariants added

1. Agents receive `SamchatVisibleTask`, which has no `criteria` attribute.
2. Rubric-only mutations do not change the agent-visible payload.
3. Phase-one contracts reject external side effects.
4. Write-mode tools require human approval and still cannot execute in the
   benchmark harness.
5. `OperationalCase.supports_fact_claim()` verifies exact fact key, exact value,
   and exact `fact_key_evidence_id`; same-case evidence is not enough.
6. Benchmark PASS requires every criterion to pass and zero side effects.

## Verification

Local focused verification:

```bash
PYTHONPATH=src python3 -m pytest tests/unit/test_assistant_specialist_contract.py -q
# 8 passed
```

Additional local hygiene:

```bash
git diff --check
PYTHONPATH=src python3 -m compileall -q   src/samchat/assistant/specialist_contract.py   src/samchat/assistant/operational_case.py   src/samchat/assistant/samchat_task_schema.py   src/samchat/assistant/specialist_harness.py   tests/unit/test_assistant_specialist_contract.py
```

The broad assistant scoped gate could not run on the host Python because the
checkout has no virtualenv and the system Python exposes Pydantic v1 while the
repo requires Pydantic v2 (`field_validator`). The GitHub workflow installs repo
requirements and is the intended broad verification gate.
