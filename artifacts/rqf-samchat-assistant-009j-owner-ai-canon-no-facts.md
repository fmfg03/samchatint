# RQF-SAMCHAT-ASSISTANT-009J - Owner Canon Is Not Live Evidence

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: prevent owner-needs canon from being answered as factual live evidence.

## Problem

The `009I` live canary improved routing to 6/10, but four prompts still failed because the assistant treated required fields as if they were known facts. Examples:

- `hoteles contratados y camas-noche` became "se han contratado...";
- `proveedores asistieron fisicamente` hallucinated Telmex Telcel;
- uniform delivery was described as generally occurring in venues instead of saying exact date/place were not evidenced.

## Fix

For `CANON_ONLY` retrieval contexts, the system context now includes an explicit rule:

- the recovered owner-needs document describes requirements/canon, not live evidence;
- if the user asks for concrete facts and the context lacks exact values, the assistant must say there is no evidence available in recovered context;
- it must name which source/tool would be needed;
- it must not convert a required field into an occurred fact.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_request_intent.py   tests/unit/test_assistant_request_router_integration.py   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_rag_search_quality.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   -q
```

Result:

- 78 passed
- 20 warnings

## Claim boundary

Established locally:

- The assistant now receives an explicit canon-vs-evidence boundary for owner-needs prompts.
- The boundary is protected by a product canon contract test.

Pending:

- Promote to live canary.
- Rerun owner-needs prompt set.
