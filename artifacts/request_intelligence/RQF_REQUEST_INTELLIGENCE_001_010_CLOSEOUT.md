# RQF Request Intelligence 001-010 Closeout

## Branch/Base

- Branch: `rqf/file-intelligence-001-025-clean-v2`
- Starting HEAD: `2e6e9bd1123fcb5e40c17958b2e9c3783f9791a2`
- Base includes live Assistant runtime and file-intelligence modules.

## Files Changed

- `src/samchat/assistant/request_intent.py`
- `src/samchat/assistant/request_router.py`
- `src/samchat/assistant/request_reports.py`
- `src/samchat/assistant/request_response.py`
- `src/samchat/assistant/conversation_service.py`
- `tests/unit/test_assistant_request_intent.py`
- `tests/unit/test_assistant_request_reports.py`
- `tests/unit/test_assistant_request_router_integration.py`
- `tests/unit/test_assistant_request_export_guard.py`
- `artifacts/request_intelligence/RQF_REQUEST_INTELLIGENCE_001_010_CLOSEOUT.md`

## Deterministic Intents Supported

- Finance comparison: `Compara gasto 2026 vs 2025 por concepto`
- Finance breakdown/list pending: gastos por proveedor/proyecto/torneo/concepto, gastos pendientes
- CFDI/facturas: pending, unlinked, without expense, payment status
- Payments/comprobaciones/reembolsos: pending and due soon
- Tournament/team/player registration status: incomplete documents and pending review
- Executive summary/risk prompts: deterministic read-only route

Unsupported or ambiguous prompts are not guessed. They fall through to existing provider behavior only when the request is not detected as an operational deterministic intent.

## Read-Only Seams Used

- Finance year-over-year comparison uses `finance_query_service.run_read_only_comparison`.
- Other supported domains route to existing read-only canonical actions:
  - `executive.realtime_report`
  - `receipts.cfdi_matching_overview`
  - `receipts.pending_payment_overview`
  - `operations.tournament_soul_snapshot`
  - `executive.planner_snapshot`

Runtime execution uses the existing injected read-only `action_router` executor. No direct write adapters were added.

## Provider Bypass Proof

The request router integration tests pass provider sentinel functions that raise if called. Deterministic finance and CFDI/payment paths return without invoking those sentinels.

## Export Guard Proof

Export prompt is added only when:

- report status is `success`
- rows exist
- `exportable=true`

Export prompt is not added for:

- provider timeout/error messages
- empty results
- unavailable data source
- clarification/unsupported responses

## Validation Results

- `python3 -m py_compile src/samchat/assistant/request_intent.py src/samchat/assistant/request_router.py src/samchat/assistant/request_reports.py src/samchat/assistant/request_response.py src/samchat/assistant/conversation_service.py src/samchat/assistant/router.py` - passed
- `./scripts/pytestw tests/unit/test_assistant_request_intent.py` - 6 passed
- `./scripts/pytestw tests/unit/test_assistant_request_reports.py` - 5 passed
- `./scripts/pytestw tests/unit/test_assistant_request_router_integration.py` - 5 passed
- `./scripts/pytestw tests/unit/test_assistant_request_export_guard.py` - 3 passed
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py` - 15 passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py` - 6 passed
- `./scripts/pytestw tests/unit/test_assistant_document_live_wiring.py` - 7 passed

## Safety

- Frozen Assistant UI untouched: no changes to `goal-fest-page/src/pages/Assistant.tsx`, `goal-fest-page/dist`, or `artifacts/ui`.
- No DB model changes.
- No auth/session changes.
- No migrations.
- No OCR commit-path changes.
- No webhook changes.
- No provider behavior changed.
- No direct adapter write behavior changed.
- Deterministic request routing remains read-only and fail-closed.

## Remaining Unsupported Requests/Debt

- Some non-finance read-only routes depend on an injected action-router executor at runtime; without it, the response is `data_source_unavailable`.
- Tournament prompts without a tournament context may require clarification before a precise snapshot.
- Executive summaries are deterministic read-only snapshots; provider wording polish remains optional and is not required for correctness.
