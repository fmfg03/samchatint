# RQF-SAMCHAT-ASSISTANT-009F - Owner AI Needs Routing Fix

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: assistant routing quality for owner-needs folder questions.

## Problem found in live canary smoke

A live read-only smoke request against `/api/assistant` asked:

```text
Que debe contener una carpeta por entidad para cualquier torneo?
```

Observed live behavior:

- HTTP: ok
- Latency: 2.539s
- Pending confirmation: null
- Writes attempted: false
- Route/tool behavior: deterministic `operations.tournament_soul_snapshot`
- Answer: active tournament status table

This was safe but incorrect. The assistant treated a product/canon folder-definition question as a live tournament status request.

## Fix

`_assistant_classify_request` now recognizes owner-needs conceptual vocabulary:

- carpeta por entidad;
- carpeta de la entidad;
- carpeta de fase nacional;
- fase nacional;
- camas-noche;
- box lunch;
- activacion de marcas;
- visitantes involucrados;
- fotografias;
- necesidades del dueño/dueno.

When this vocabulary is paired with conceptual/request-for-definition language such as `que debe contener`, `que datos`, `como debe responder`, `que puede hacer`, or `sin cambiar datos`, the route becomes RAG-only:

- route: `lookup_sql`
- domain: `generic`
- rag_only: true
- tool definitions: none

Write-like owner prompts remain on the authority path. Example:

```text
Crea la carpeta de la entidad Jalisco para el torneo de beisbol 2026
```

This remains non-RAG-only with write intent, so it must go through preview/authority rather than being answered as mere documentation.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_rag_search_quality.py   tests/unit/test_assistant_product_canon_contract.py   -q
```

Result:

- 49 passed
- 9 warnings

## Claim boundary

Established locally:

- Conceptual owner-needs folder questions no longer receive live status tools.
- The assistant can route these questions to curated context/RAG without tools.
- Write-like folder creation prompts still preserve the authority path.

Not yet established:

- The production service has been restarted/deployed with this local commit.
- The 10-prompt owner canary has been rerun successfully against the live endpoint after deployment.
