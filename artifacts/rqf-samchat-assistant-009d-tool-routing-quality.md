# RQF-SAMCHAT-ASSISTANT-009D ? Tool Routing Quality

Status: CLOSED_LOCAL
Date: 2026-07-30

## Objective

Improve canary response quality and latency by reducing unnecessary remote-provider/tool overhead while preserving the authority boundary.

## Implementation

- Added a local-fast provider policy for `lookup_sql` and `needs_clarification` routes.
- The policy defaults those latency-sensitive read-only routes to `ollama_first`, even when the global provider setting is remote-only.
- Explicit route overrides still win: `ASSISTANT_LLM_PROVIDER_ROUTE_REMOTE_ONLY=lookup_sql` keeps lookup remote when the operator deliberately asks for it.
- Added `rag_only` classification for product/canon questions about SamChat, Claude Code paradigm, dashboard-vs-assistant identity, methodology, and release QA.
- `rag_only` turns use recovered context and conversation memory with zero provider tool schemas.
- Code/repo questions remain `code_agentic`; the product-context detector does not hijack real code work.

## Runtime boundary

- No writes enabled.
- No allowlist expansion.
- No database migrations.
- No provider secrets changed.
- Behavior remains configurable by environment variables.

## Env-real verification

With `/etc/samchat/samchat.env` currently setting global provider to `anthropic_only`:

```text
Read-only checklist for solicitud de transferencia
route=lookup_sql domain=finance rag_only=false
providers=ollama,anthropic,openai
tools=10

SamChat dashboard con chat vs Claude Code
route=lookup_sql domain=generic rag_only=true
providers=ollama,anthropic,openai
tools=0

Repo frontend endpoint del asistente
route=code_agentic domain=code rag_only=false
providers=anthropic
tools=5
```

## Tests

```text
PYTHONPATH=src:. /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/test_assistant_inference_router.py   tests/unit/test_assistant_case_memory.py   tests/unit/test_assistant_product_canon_contract.py   tests/unit/test_assistant_curated_rag_ingest.py   tests/unit/test_assistant_rag_search_quality.py -q

51 passed, 9 warnings
```

## Expected quality impact

- Fewer provider timeouts for short read-only turns.
- Less Anthropic tool-schema exposure for product/canon questions.
- Faster canon answers through local-first RAG-only turns.
- Remote model remains available as fallback for lookup unless explicitly disabled.
