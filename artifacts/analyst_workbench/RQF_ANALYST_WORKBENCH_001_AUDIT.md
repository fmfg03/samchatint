# RQF Analyst Workbench 001 Audit

## Safe Insertion Point

`conversation_service.py` already enforces deterministic short-circuits before provider fallback:

1. document upload rendering from `DOCUMENT_INTAKE_RESULT`
2. document confirmation/cancel commands
3. deterministic request intelligence
4. finance comparison short-circuit
5. generic provider-backed assistant turn

Analyst Workbench can be inserted after request intelligence and before the finance legacy/provider fallback. This preserves document-intelligence and request-intelligence priority.

## Non-Interference Strategy

- Analyst intent detection should treat operational write/action phrases as operational-route hints, not analyst work.
- `conversation_service.py` should run `detect_request_intent()` first and return deterministic request responses before Analyst.
- Document confirmation commands are parsed before Analyst.
- `DOCUMENT_INTAKE_RESULT` upload handling remains before Analyst.
- The existing request rule for broad `riesgos` needed narrowing so document risk-review prompts like `Qué riesgos ves en este contrato` can reach Analyst instead of the executive operational route.

## Safe Context Sources

Available without new DB tables or UI changes:

- explicit user-provided text in the current message
- uploaded document text already included in conversation messages
- `DOCUMENT_INTAKE_RESULT` summaries and extracted entities
- prior assistant messages containing deterministic report tables/summaries
- existing conversation message history loaded through `AssistantMessage`

RAG helpers exist under `src/samchat/assistant/rag.py` and router RAG endpoints, but this tranche does not need to call or mutate RAG state.

## Provider Use

Runtime provider use is optional and must only happen after deterministic operational routes decline the request. For v1, the workbench can provide template-based context synthesis when context is available. Tests will not call providers.

If context is insufficient and no provider function is explicitly injected, Analyst should return `needs_context` rather than inventing an answer.

## Unsupported In V1

- No new persistent analyst memory.
- No UI changes.
- No export for analyst prose.
- No action execution.
- No direct RAG mutation/ingest.
- No live provider calls in tests.
