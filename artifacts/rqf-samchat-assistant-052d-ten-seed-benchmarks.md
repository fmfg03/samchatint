# RQF-ASSISTANT-052D ? Ten Plataforma Specialist Seed Benchmarks

Status: IMPLEMENTED_LOCAL

## Scope

This slice expands the specialist benchmark seed set from 3 to 10 unique
Plataforma-shaped tasks, covering all initial `operational_case` types named in
RQF-ASSISTANT-052:

- tournament;
- team;
- player_validation;
- document_incident;
- money_request;
- expense_report;
- budget;
- supplier.

## Benchmarks added/covered

1. `SAMCHAT-FIN-AMEX-001` ? AMEX expense report / REF 28.
2. `SAMCHAT-OWNER-DCC-001` ? DCC owner entity folder / CxC context.
3. `SAMCHAT-SUPPLIER-HOTEL-001` ? lodging tax / ISH supplier precedent.
4. `SAMCHAT-TEAM-REG-001` ? team registration facts and third-page rule.
5. `SAMCHAT-PLAYER-ELIG-001` ? player eligibility without guessing person data.
6. `SAMCHAT-DOC-INCIDENT-001` ? duplicate CURP/document incident review.
7. `SAMCHAT-MONEY-REQ-001` ? money request with operations/system references.
8. `SAMCHAT-BUDGET-2027-001` ? annual budget preview from historical evidence.
9. `SAMCHAT-TOURNAMENT-2027-001` ? tournament creation preview from prior year.
10. `SAMCHAT-CXC-COLLECTION-001` ? CxC + collection account preview.

## Safety properties

- All 10 tasks remain read-only / preview-only.
- Private rubrics remain hidden from the agent-visible task payload.
- Every benchmark requires verified `amount`, `supplier`, and `account` proposal
  facts where applicable to the current v0 Finance preview abstraction.
- The suite asserts unique task ids to avoid padding the benchmark count.
- The negative rubric mutation test still fails closed when expected facts are
  wrong.

## Verification

```bash
PYTHONPATH=src python3 -m pytest   tests/unit/test_assistant_specialist_contract.py   tests/unit/test_assistant_specialist_agents.py   tests/unit/test_assistant_specialist_benchmarks.py -q
# 22 passed
```
