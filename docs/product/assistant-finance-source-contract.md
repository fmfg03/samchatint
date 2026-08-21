# Assistant Finance Source Contract

Scope: F4 D1-S6. This document defines finance-answer source authority for the
assistant and records the implemented read-only runtime surfaces. It does not
authorize actions, exports, writes, or finance mutations.

## Operating Rule

The assistant must answer finance questions from canonical Finance Spine read
models. It must not recompute AR or cashflow through loose SQL, legacy
accounting routes, or pre-matching candidates.

Finance answers must cite the read model or source fields used. When a source
returns `source_notes`, the assistant should surface material caveats instead
of hiding them behind a confident answer.

## Intent Map

| intent | source function | allowed fields | required labels | forbidden interpretations |
|---|---|---|---|---|
| `ar.summary` | `build_ar_read_model(...)` | `expected_income`, `issued_linked`, `issued_unlinked`, `collection_gaps`, `matching_gaps`, `summary` | expected income, issued/recognized income, cobranza AR probada, collection unknown | do not treat `collection_unknown` as collected; do not infer outstanding cash without match authority |
| `ar.prematching` | `build_ar_matching_workbench(...)` | `items`, `accepted_matches`, `unmatched_bank_inflows`, `summary`, `source_notes` | evidencia candidata, manual review, accepted matches | do not treat `candidate_match` as cobrado; do not mark collection from bank evidence alone |
| `cashflow.summary` | `build_cashflow_planning_read_model(...)` | `summary`, `monthly_buckets`, `source_notes` | caja real, obligaciones AP, ingreso reconocido, cobranza AR probada, ingreso esperado no cobrado, forecast derivado | do not use AR candidates as cash; do not treat forecast as actual cash |
| `budget.snapshot` | canonical budget services, including `build_budget_snapshot(...)` | budget versions, lines, monthly plan, actuals, forecast fields when already present | presupuesto, plan mensual, real, varianza | do not mix expense and income lines without labels |
| `finance.platform` | `build_finance_source_snapshot(...)` and `build_finance_platform_snapshot(...)` | cash control, payment run, accounting close, tax readiness, finance brief | AP/payment run, cierre contable, tax readiness | do not reuse AP/payment-run as AR inverse |
| `finance.exports` | `_finance_export_catalog(...)` | export id, owner, route, route template/family, artifact class, status, caveat | guidance only, owner module, route, status | do not generate files; do not call exporters; do not treat legacy cashflow export as Finance Spine authority |
| legacy accounting cashflow | none for assistant authority | none | legacy/reference only | `/admin/contabilidad/cash-flow` is not a canonical assistant finance source |

## Language Rules

`matched_collected`

- Means cobranza AR probada.
- It may support collected income, collection date, cashflow, planning, and
  outstanding-balance answers.

`candidate_match`

- Means evidence candidate only.
- It must never be described as cobrado, paid, collected, or cash received.

`collection_unknown`

- Means the assistant must not infer collection or a confirmed outstanding
  balance.
- It may be described as a gap requiring match authority.

`recognized_income`

- Means income recognized through CFDI/budget income linkage.
- It is not the same as cash collected.

`forecast_net`

- Means forecast derivado.
- It must be labeled as derived, not actual cash.

## Implemented Read-Only Runtime

F4 S1-S6 are implemented as read-only assistant surfaces:

- Adapter: `run_finance_read_adapter(...)` in
  `src/samchat/assistant/finance_read_adapter.py`.
- Tool: `assistant_finance_read` in `src/samchat/assistant/router.py`.
- Renderer: `render_finance_read_answer(...)` in
  `src/samchat/assistant/finance_read_answer.py`.

Implemented intents:

- `ar.summary`
- `ar.prematching`
- `cashflow.summary`
- `budget.snapshot`
- `finance.platform`
- `finance.exports`

Runtime guarantees:

- `assistant_finance_read` is a read tool and a finance read tool.
- It is not a write tool and does not create pending confirmations.
- It calls the canonical adapter, which calls the canonical read models.
- It returns answer text through the deterministic renderer for these finance
  reads instead of relying on free-form model interpretation.
- It does not add routes, POST handlers, schemas, direct file generation, or
  finance state.

## Safety Rules

- F4 read surfaces are read-only.
- No actions.
- No POST.
- No assistant writes.
- No assistant-side mutation of finance, budget, bank, AR, payment run, or
  accounting state.
- No direct export file generation from `assistant_finance_read`.
- Export execution remains owned by the source module and route.
- No legacy `/admin/contabilidad/cash-flow` as authority.
- No loose SQL to recompute AR or cashflow when canonical read models exist.
- Any future write-like assistant action requires preview, confirmation,
  authority, and receipt.

## Pending F4 Work

Still out of scope after F4 S6:

- write-like finance actions;
- direct export execution or file generation from chat;
- export archiving as managed artifacts;
- UI beyond existing assistant runtime;
- replacing `finance_ops_query` for unrelated general finance questions.

## F4 Closeout Boundary

F4 read-only/guidance is complete. The assistant finance copilot has one
bounded read tool, one adapter, and one deterministic renderer for these
implemented intents:

- `ar.summary`
- `ar.prematching`
- `cashflow.summary`
- `budget.snapshot`
- `finance.platform`
- `finance.exports`

F4 does not authorize:

- finance writes;
- budget mutations;
- AR collection recognition outside accepted match authority;
- payment execution;
- COI or DIOT execution;
- direct file generation from chat;
- export archiving as managed artifacts.

Any next assistant finance step requires a new story and spec. Direct export
execution, artifact archiving, payment-run actions, COI/DIOT actions, budget
changes, AR collection authority, and other write-like finance behavior are
separate scopes.

## Contract Tests

F4 behavior is protected by:

- `tests/unit/test_assistant_finance_read_adapter.py`
- `tests/unit/test_assistant_finance_read_answer.py`
- `tests/unit/test_assistant_finance_read_tool_wiring.py`
- `tests/unit/test_assistant_agent_runtime_contract.py`
