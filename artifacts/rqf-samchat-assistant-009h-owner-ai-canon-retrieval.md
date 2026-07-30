# RQF-SAMCHAT-ASSISTANT-009H - Owner AI Canon-Only Retrieval

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: retrieval quality for owner-needs conceptual prompts.

## Problem

After `009G`, live routing was safe and reached RAG-only mode, but the answer still mixed entity-folder requirements with national-phase requirements. The trace showed memory snippets outranking the owner canon document.

This created a quality risk: previous conversation memory could contaminate canonical product answers.

## Fix

For owner-needs conceptual requests, `_build_hybrid_retrieval` now uses `canon_only` retrieval:

- skips SQL snippets;
- skips memory snippets;
- filters docs to `/docs/assistant/` sources;
- uses a separate cache key so old hybrid results do not contaminate canon-only answers.

The owner-needs document also separates Spanish retrieval anchors:

- per-entity vocabulary stays near the per-entity folder section;
- national-phase vocabulary stays near the national-phase folder section.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_request_intent.py   tests/unit/test_assistant_request_router_integration.py   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_rag_search_quality.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   -q
```

Result:

- 74 passed
- 20 warnings

## Claim boundary

Established locally:

- Owner-needs conceptual prompts use a document/canon-only retrieval mode.
- Conversation memory no longer outranks the owner canon for this class of request.
- Existing deterministic finance/tournament read-only routes remain covered by tests.

Pending:

- Promote to live canary.
- Rerun owner-needs smoke and 10-prompt set.
