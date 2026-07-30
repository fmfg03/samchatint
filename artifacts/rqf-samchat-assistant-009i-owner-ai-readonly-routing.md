# RQF-SAMCHAT-ASSISTANT-009I - Owner AI Read-Only Routing Expansion

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: route owner-needs read-only questions away from premature operational tools.

## Problem

The 10-prompt live canary on `8ad4e1f14` passed 4/10. The main remaining routing failures were:

- planning prompt with `sin cambiar datos` was interpreted as a mutation because it contains the token `cambiar`;
- specific owner-needs questions such as uniform delivery, camas-noche, medical services, sponsor visitors, and brand activation providers were routed as generic tournament/finance questions before the dedicated owner-folder tools exist.

## Fix

Expanded owner-needs context detection and made read-only owner-needs prompts RAG-only unless they contain affirmative write intent.

Added terms include:

- `carpetas del torneo`;
- `fase estatal`;
- `entrega de uniformes`;
- `uniformes de fase estatal`;
- `servicios medicos`;
- `accidentes con traslado`;
- `activaciones de marca`;
- `evidencia fotografica`.

Added negated-mutation handling:

- `sin cambiar datos`;
- `sin cambiar nada`;
- `no cambies datos`;
- `no modifiques datos`.

These phrases explicitly suppress write/code-change routing for owner-needs planning prompts.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_request_intent.py   tests/unit/test_assistant_request_router_integration.py   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_rag_search_quality.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   -q
```

Result:

- 77 passed
- 20 warnings

## Claim boundary

Established locally:

- Owner-needs read-only questions avoid premature business tools while dedicated folder tools are not yet wired.
- Explicit no-mutation planning stays read-only/RAG-only.
- Existing deterministic read-only finance/tournament routes remain covered.

Pending:

- Promote to live canary and rerun owner-needs prompt set.
