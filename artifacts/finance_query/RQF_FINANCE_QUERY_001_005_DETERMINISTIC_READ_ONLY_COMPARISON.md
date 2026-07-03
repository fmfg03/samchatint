# RQF Finance Query 001-005 Closeout

## Scope

Implemented a deterministic read-only finance comparison path for Assistant messages like:

- `Compara gasto 2026 vs 2025 por concepto`
- `compara gastos 2025 contra 2026 por categoria`
- `gasto por concepto 2026 vs 2025`
- `variacion de gastos por concepto entre 2025 y 2026`

The path runs before provider fallback in `conversation_service.py`.

## Deterministic Contract

Detected intent fields:

- metric: `gasto`
- years: first two years in the user message
- group_by: `concepto`, `category`, or `account`
- comparison: `year_over_year`

Response shape on success:

- compact markdown table
- columns: `Concepto`, base year, comparison year, `Dif.`, `Var. %`
- export prompt only when the result status is `success` and rows exist

If there are no rows or the data source is unavailable, the response states that directly and does not offer export.

## Read-Only Source

Primary source seam:

- `src/samchat/assistant/finance_query_service.py`
- read-only aggregation over the existing gastos `ExpenseReport` read model when a session is available

Tests inject a read-only rows provider so no live DB is touched.

No raw writes, migrations, model changes, auth changes, OCR paths, webhooks, provider calls, or UI files were changed.

## Provider Fallback

The deterministic finance path bypasses provider execution for mapped comparison requests. Tests use provider sentinels that raise if called.

If a message cannot be mapped to the deterministic finance intent, existing Assistant behavior remains unchanged.

## Export Prompt Conditions

Export prompt appears only when:

- deterministic finance result status is `success`
- at least one result row exists
- the trace contains exportable rows

Export prompt is suppressed when:

- provider timeout message is returned
- result is empty
- data source is unavailable
- no exportable trace rows exist

## Validation

Commands run:

- `python3 -m py_compile src/samchat/assistant/finance_query_intent.py src/samchat/assistant/finance_query_service.py src/samchat/assistant/router.py src/samchat/assistant/conversation_service.py`
- `./scripts/pytestw tests/unit/test_assistant_finance_query_intent.py` - 4 passed
- `./scripts/pytestw tests/unit/test_assistant_finance_query_read_only.py` - 3 passed
- `./scripts/pytestw tests/unit/test_assistant_finance_query_router_integration.py` - 4 passed
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py` - 15 passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py` - 6 passed

## Safety Statement

No writes were added. No backend auth, DB model, OCR, webhook, provider, or frozen Assistant UI behavior was changed.

The query `Compara gasto 2026 vs 2025 por concepto` now takes a deterministic read-only path before provider fallback. It returns an export option only after a successful result with rows.
