# RQF-ASSISTANT-052C ? Plataforma Seed Benchmarks

Status: IMPLEMENTED_LOCAL

## Scope

This slice adds the first Plataforma-shaped benchmark fixtures for the
specialist-agent loop:

1. `SAMCHAT-FIN-AMEX-001` ? AMEX expense report / REF 28 style finance preview.
2. `SAMCHAT-OWNER-DCC-001` ? owner/DG entity-folder and CxC tournament context.
3. `SAMCHAT-SUPPLIER-HOTEL-001` ? supplier precedent for lodging tax / ISH.

Each benchmark includes:

- `SamchatTask` with private rubric;
- one or more `OperationalCase` fixtures;
- exact evidence bindings for facts;
- execution through the same Knowledge ? Verifier ? Finance handoff;
- all-pass evaluation;
- explicit preview-only authority boundary.

## Non-claims

These are deterministic seed fixtures, not live production data retrieval. They
are regression seeds for architecture and evidence semantics. Live corpus
indexing, DB-backed retrieval, and LLM specialist execution remain future cuts.

## Safety properties

- The private rubric is not exposed to the agent-visible task payload.
- Finance outputs remain `execution_allowed=false`.
- Missing evidence remains visible as a gap instead of being invented.
- The negative test mutates the expected rubric and proves the evaluator fails
  when the workflow does not produce that expected item.

## Verification

```bash
PYTHONPATH=src python3 -m pytest   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py   tests/unit/test_assistant_specialist_benchmarks.py -q
# 20 passed
```
