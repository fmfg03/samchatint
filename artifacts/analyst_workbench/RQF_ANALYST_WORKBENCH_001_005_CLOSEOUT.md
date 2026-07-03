# RQF Analyst Workbench 001-005 Closeout

## Branch/Base/Head

- Branch: `rqf/analyst-workbench-001-005`
- Base HEAD: `db855a3e4283a352623685b15f2a0cf88271a9da`
- Base branch source: `rqf/file-intelligence-001-025-clean-v2`

## Files Changed

- `src/samchat/assistant/analyst_intent.py`
- `src/samchat/assistant/analyst_workbench.py`
- `src/samchat/assistant/analyst_response.py`
- `src/samchat/assistant/conversation_service.py`
- `src/samchat/assistant/request_intent.py`
- `tests/unit/test_assistant_analyst_intent.py`
- `tests/unit/test_assistant_analyst_workbench.py`
- `tests/unit/test_assistant_analyst_routing_integration.py`
- `tests/unit/test_assistant_analyst_provider_isolation.py`
- `artifacts/analyst_workbench/RQF_ANALYST_WORKBENCH_001_AUDIT.md`
- `artifacts/analyst_workbench/RQF_ANALYST_WORKBENCH_001_005_CLOSEOUT.md`

## Analyst Intents Supported

- `explain`
- `risk_review`
- `compare`
- `summarize`
- `questions`
- `next_steps`

The workbench requires context from uploaded documents, document-intake summaries, report outputs, or conversation text. Without context, it asks the user to upload, paste, or select material.

## Routing Priority Proof

`conversation_service.py` now routes in this order:

1. Document upload/intake rendering
2. Document confirmation/cancel commands
3. Deterministic request intelligence
4. Analyst Workbench
5. Legacy finance comparison guard
6. Generic provider fallback

Tests prove finance, CFDI, payment, and confirmation-command paths do not enter Analyst.

## Operational Non-Interference

The Analyst detector returns an operational-route hint instead of claiming:

- `Compara gasto 2026 vs 2025 por concepto`
- `Qué CFDIs están pendientes`
- `Qué pagos vencen esta semana`
- `CONFIRMAR accion abc123`

The `request_intent` executive-risk rule was narrowed so document risk-review prompts such as `Qué riesgos ves en este contrato` can reach Analyst instead of the executive operational route.

## Provider Isolation Proof

Tests use provider sentinels that raise if provider fallback is reached by deterministic operational routes. Analyst provider use is optional; when an injected provider raises, the workbench returns `provider_unavailable` safely without executing actions.

## Validation Results

- `python3 -m py_compile src/samchat/assistant/analyst_intent.py src/samchat/assistant/analyst_workbench.py src/samchat/assistant/analyst_response.py src/samchat/assistant/conversation_service.py src/samchat/assistant/router.py` - passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_intent.py` - 7 passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_workbench.py` - 4 passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_routing_integration.py` - 5 passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_provider_isolation.py` - 2 passed
- `./scripts/pytestw tests/unit/test_assistant_request_intent.py` - 6 passed
- `./scripts/pytestw tests/unit/test_assistant_request_router_integration.py` - 5 passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py` - 6 passed
- `./scripts/pytestw tests/unit/test_assistant_document_live_wiring.py` - 7 passed

## Safety

- Frozen UI untouched: no changes to `goal-fest-page/src/pages/Assistant.tsx`, `goal-fest-page/dist`, or `artifacts/ui`.
- No DB model changes.
- No auth/session changes.
- No migrations.
- No OCR commit-path changes.
- No webhook changes.
- No write execution.
- No action_router write actions called.
- No provider calls in tests.
- No chain-of-thought/private reasoning exposed.

## Remaining Debt

- Analyst v1 is mostly deterministic/template-based; provider wording can be enabled later behind policy if needed.
- RAG/context snippets are not pulled directly by this tranche; Analyst uses already available conversation/document/report context.
- Multi-document comparison is caveated when only one source is available.
- Analyst answers are not exportable by default.
