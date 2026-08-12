# RQF-ASSISTANT-052B ? Knowledge ? Verifier ? Finance Handoff

Status: IMPLEMENTED_LOCAL

## Scope

This slice turns the RQF-052A schema into the first executable specialist-agent
handoff:

1. `InstitutionalKnowledgeAgent` emits candidate claims from operational cases.
2. `EvidenceVerifierAgent` validates each claim against exact fact/value/evidence
   bindings.
3. `FinanceAgent` produces preview-only finance proposal items only from
   supported claims.
4. `run_specialist_workflow()` composes the three steps in sequence.

## Authority boundary

The workflow remains read-only/proposal-only:

- no external side effects;
- no database writes;
- no accounting posts;
- finance output is `preview_only` and `execution_allowed=false`;
- authority remains `human_approval_required`.

## Safety invariants

- Knowledge does not invent unsupported facts; facts without explicit evidence
  become `missing_evidence` instead of claims.
- Verifier rejects adulterated values even when evidence exists in the same case.
- Verifier rejects correct values paired with another fact's evidence.
- Finance ignores all unverified claims.
- The workflow status fails closed if the verifier rejects any handoff claim.

## Verification

```bash
PYTHONPATH=src python3 -m pytest   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py -q
# 13 passed
```
