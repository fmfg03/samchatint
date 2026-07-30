# RQF-SAMCHAT-ASSISTANT-009G - Owner AI Deterministic Router Bypass

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: deterministic request router bypass for owner-needs conceptual questions.

## Problem

After deploying `7cff14299`, the live smoke still answered:

```text
Que debe contener una carpeta por entidad para cualquier torneo?
```

with `operations.tournament_soul_snapshot`. This showed an earlier deterministic router intercepted the request before the RAG/product-canon classifier.

## Fix

Added `is_owner_ai_conceptual_request` in `samchat.assistant.request_intent` and excluded those prompts from deterministic tournament status intent detection.

Conceptual owner-needs prompts now proceed to the normal assistant/RAG path instead of being converted into a live tournament snapshot.

## Preserved behavior

The bypass is narrow. It requires both owner-needs vocabulary and conceptual wording such as:

- `que debe contener`;
- `que datos`;
- `como debe responder`;
- `que puede hacer`;
- `sin cambiar datos`.

Operational tournament status requests still use deterministic read-only routing.

## Tests

Command:

```bash
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_request_intent.py   tests/unit/test_assistant_request_router_integration.py   tests/unit/test_assistant_inference_router.py   -q
```

Result:

- 57 passed
- 12 warnings

## Claim boundary

Established locally:

- Owner folder-definition prompts do not bypass the provider/RAG path as tournament status.
- The deterministic tournament read-only route remains available for ordinary operational status requests.

Pending:

- Promote this commit to canary live.
- Rerun the owner-needs canary prompt set.
