# Canonical Finance Map

Status: Draft v0.1
Date: 2026-08-20
Scope: F1 product/technical map only. No runtime, schema, route, permission, or data changes.

## Authority

The canonical Finance Spine is the set of live finance routes and services that should own future AR, cashflow, planning, and assistant-finance work.

Canonical surfaces:

- `/admin/finanzas` in `src/devnous/gastos/routes/admin_routes.py`.
- `/admin/finanzas/payment-run` and related payment-run routes in `src/devnous/gastos/routes/admin_routes.py`.
- `/admin/presupuestos` registered through `register_presupuestos_routes(router)` in `src/devnous/gastos/routes/admin_budget_routes.py`.
- `src/samchat/finance_platform/service.py` for finance action queue, cash control, accounting close, tax readiness, payment run, finance brief, and finance copilot prompts.
- `src/samchat/budgets/service.py` for canonical budget versions, lines, concepts, monthly plan, actuals, income direction, and budget schema.
- `src/devnous/gastos/services/cfdi_income_bridge_service.py` for PSP CFDI income candidates and budget CFDI income links.
- `src/samchat/sam_inbox/service.py` for finance projection into the cross-module operating inbox.

Legacy or secondary surfaces:

- `/admin/presupuestos-legacy` in `src/devnous/gastos/routes/admin_routes.py`.
- older `/admin/presupuestos/*` handlers still present in `src/devnous/gastos/routes/admin_routes.py`.
- broad roadmap documents that mention finance capabilities without a route/service owner.

Rule: new finance work should extend canonical services first. Legacy presupuesto code must not be treated as the owner for new AR, cashflow, or planning behavior unless a separate approved cleanup spec says so.

## Surface Map

| surface | route_or_service | status | canonical_owner | reads | writes | feeds_next_lane | risk |
|---|---|---:|---|---|---|---|---|
| Finance command center | `/admin/finanzas` | live | `admin_routes.py` + `finance_platform` | documents, expenses, polizas | form posts for payment, COI classification, CFDI links | cashflow, accounting close, assistant finance | A single page mixes AP, COI, DIOT, cash pressure, and brief. |
| Finance platform snapshot | `build_finance_source_snapshot`; `build_finance_platform_snapshot` | live | `src/samchat/finance_platform/service.py` | Documento, ExpenseReport, AccountingPoliza | none | cashflow, Sam Inbox, assistant finance | Read model currently labels income from income polizas, not full AR. |
| Payment run | `/admin/finanzas/payment-run` and payment-run posts | live | `admin_routes.py` + documento payment services | approved unpaid documents | payment dates, closures, pay actions | cashflow AP obligations | Must not be reused as AR inverse. |
| COI pending classification | `/admin/finanzas/coi-pendientes/clasificar` | live | `admin_routes.py` | expenses needing accounting accounts | account assignments | accounting close | Mutating accounting metadata must remain permissioned. |
| DIOT blockers / CFDI manual links | `/admin/finanzas/diot-blockers/link-cfdi` | live | `admin_routes.py` | documents and expenses missing CFDI | manual UUID/CFDI association | tax readiness | Fiscal state is not equivalent to cash state. |
| Canonical budgets dashboard | `/admin/presupuestos` | live | `admin_budget_routes.py` + `samchat.budgets` | budget versions, concepts, lines, monthly plan, actuals | version, metadata, line, concept, import/link actions | AR, cashflow, planning | Route namespace also has legacy handlers elsewhere. |
| Budget tournament detail | `/admin/presupuestos/torneo/{tournament_key}` | live | `admin_budget_routes.py` | expense/income budget lines, actuals, monthly plan | income imports, line updates, CFDI income links | AR, cashflow, project planning | Must label expense vs income directions clearly. |
| Budget income import/export | `/admin/presupuestos/torneo/{tournament_key}/ingresos/*` | live | `admin_budget_routes.py` + budget exporter | income budget lines | income imports | AR expected-income baseline | Imported expected income is not issued invoice state. |
| CFDI income links | `cfdi_income_bridge_service.py`; `/cfdi-ingresos/*` routes | live | `cfdi_income_bridge_service.py` + `admin_budget_routes.py` | PSP CFDI income candidates, budget links | link, upload-link, soft-unlink | AR actual/issued-income reconciliation | A CFDI link is not sufficient to prove collection timing unless cash state is defined. |
| Budget monthly plan | `budget_line_monthly_plan`; `build_budget_monthly_plan_rollups` | live | `samchat.budgets.service` | monthly expected income and expense plan | monthly plan replacement/update via budget flows | cashflow planning, forecast | Needs clear separation from actuals. |
| Budget actuals | `build_budget_monthly_actuals` | live | `samchat.budgets.service` | documentos, expenses, CFDI income links | none in read model | cashflow, variance, planning | Actual buckets need labels for document, expense, CFDI income, and collection. |
| Sam Inbox finance projection | `build_sam_inbox_payload`; `_finance_items_from_platform` | live | `src/samchat/sam_inbox/service.py` | finance platform snapshot | none | operating queue, assistant routing | It is a projection, not the source of finance truth. |
| Assistant finance adapter | finance tools in `src/samchat/assistant/router.py` and `tools.py` | live | `src/samchat/assistant/` | canonical finance routes/services/tools | governed tool calls only | assistant finance copilot | Must cite read models and not recompute parallel finance state. |
| Presupuestos legacy | `/admin/presupuestos-legacy`; old handlers in `admin_routes.py` | obsolete_or_secondary | legacy `admin_routes.py` block | legacy budget data/views | legacy budget mutations | none until cleanup | Can confuse ownership if extended. |

## Finance Domains

### AP / Payment Run

Canonical owner: `/admin/finanzas/payment-run` and related payment/document services.

Purpose:

- identify approved unpaid documents;
- manage payment-run closures;
- support operational payment actions.

Boundary:

- AP is about obligations to pay out.
- AP is not AR, income, or forecast.

### COI / Accounting Close

Canonical owner: `/admin/finanzas`, `finance_platform`, accounting routes/services.

Purpose:

- surface expenses missing accounting accounts;
- identify unbalanced polizas;
- support close preparation.

Boundary:

- accounting readiness is not cashflow readiness.
- account classification mutations need explicit permissions.

### DIOT / CFDI Blockers

Canonical owner: `/admin/finanzas`, `finance_platform`, CFDI-related services.

Purpose:

- surface missing fiscal evidence;
- flag cross-month CFDI issues;
- support fiscal readiness.

Boundary:

- a CFDI/UUID closes fiscal evidence, not necessarily payment or collection.

### Budgets Expense

Canonical owner: `/admin/presupuestos`, `src/samchat/budgets/service.py`.

Purpose:

- manage expense budget lines, concepts, versions, monthly plan, actuals, and variance.

Boundary:

- expense budgets should not absorb income/AR semantics without `budget_direction` labels.

### Budgets Income

Canonical owner: `/admin/presupuestos/torneo/{tournament_key}` income section and `samchat.budgets.service`.

Purpose:

- manage expected income lines through `budget_direction=income`;
- import/export income budget workbooks;
- provide expected-income baseline for AR and cashflow.

Boundary:

- expected income is not issued CFDI and is not collected cash.

### CFDI Income Links

Canonical owner: `cfdi_income_bridge_service.py` and budget `cfdi-ingresos` routes.

Purpose:

- list PSP CFDI income candidates;
- link issued income CFDI to budget version, tournament, and income budget line;
- support real-income actuals.

Boundary:

- linked CFDI income supports AR, but AR also needs balance, due horizon, and collection state.

### AR Candidate

Canonical owner: not yet defined. F2 must define it.

Inputs:

- income budget lines;
- PSP CFDI income candidates;
- `budget_cfdi_income_links`;
- collection/payment state once identified;
- customer or payer identity where available.

Boundary:

- AR cannot be modeled as payment run in reverse.
- AR must separate expected income, issued CFDI, linked income, collected amount, outstanding balance, and due horizon.

### Cashflow Candidate

Canonical owner: not yet defined. F3 must define it.

Inputs:

- approved unpaid documents and payment run;
- cash control from `finance_platform`;
- budget monthly plan;
- budget actuals;
- CFDI income links;
- forecast rules once approved.

Boundary:

- cashflow must label actual cash, approved obligations, expected income, budget plan, actual income, and forecast separately.

### Sam Inbox Finance Projection

Canonical owner: `src/samchat/sam_inbox/service.py`.

Purpose:

- convert finance action queue and related projections into cross-module operating items.

Boundary:

- Sam Inbox is a consumer of finance read models, not the canonical finance service.

### Assistant Finance Adapter

Canonical owner: `src/samchat/assistant/`.

Purpose:

- answer finance questions;
- route users to live finance modules;
- generate governed previews and exports where authorized.

Boundary:

- assistant finance must cite or trace the read model used;
- assistant finance must not create a parallel source of truth.

## Dependency Graph

AR depends on:

- income budget lines with `budget_direction=income`;
- PSP CFDI income candidates;
- `budget_cfdi_income_links`;
- customer or payer identity;
- collection/payment state and due horizon, once the canonical source is identified.

Cashflow depends on:

- payment run;
- approved unpaid documents;
- cash control from `finance_platform`;
- income actuals from budget CFDI income links;
- monthly plan from budget lines;
- forecast rules approved in a later spec.

Assistant finance depends on:

- finance platform snapshots;
- budget read models;
- CFDI income link read models;
- Sam Inbox projections when the user asks for operating queues;
- explicit authority/confirmation for any write-like tool call.

Do not build assistant finance by recomputing raw finance state independently when a canonical read model exists.

## Decision Backlog

### D1. Freeze Canonical Presupuesto Owner

Decision needed:

- keep `admin_budget_routes.py` as the canonical owner for `/admin/presupuestos`;
- document legacy `admin_routes.py` presupuesto handlers as deprecated, hidden, or redirect-only;
- decide whether cleanup is documentation-only or code removal later.

Decision for now: `document only`.

## D1 Presupuestos Authority Freeze

Canonical owner:

- `src/devnous/gastos/routes/admin_budget_routes.py`

Canonical helpers:

- `src/devnous/gastos/routes/admin_budget_ui.py`

Canonical service:

- `src/samchat/budgets/service.py`

Canonical income/AR base:

- `src/devnous/gastos/services/cfdi_income_bridge_service.py`

Canonical route family:

- `GET /admin/presupuestos`
- `GET /admin/presupuestos/torneo/{tournament_key}`
- `POST /admin/presupuestos/torneo/{tournament_key}/ingresos/import`
- `GET /admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx`
- `GET /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/link`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/upload-link`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/{link_id}/unlink`
- `POST /admin/presupuestos/versiones/copy-forward`

Legacy block:

- `GET /admin/presupuestos-legacy`
- old presupuesto dashboard and mutation handlers still present in `src/devnous/gastos/routes/admin_routes.py`
- old handlers for concept import/export, version create/update/transition, line import/create/update, and default import under `/admin/presupuestos/*`

Policy for now:

- `document only`
- do not remove, redirect, hide, or reorder routes in this checkpoint;
- do not use legacy handlers as source authority for new AR, cashflow, planning, or assistant finance work;
- inspect route registration and live form targets before any future cleanup.

Do not extend:

- New AR work must use income budget lines with `budget_direction=income` and the CFDI income bridge.
- New cashflow work must use canonical budget monthly plan/actuals and finance platform/payment-run read models.
- New assistant finance work must cite or trace canonical read models and must not route new behavior through legacy presupuesto handlers.

Future cleanup options:

- `hide`: keep legacy routes callable but remove navigation/form references after route-target validation.
- `redirect`: redirect legacy entrypoints to canonical routes when parameter compatibility is proven.
- `remove later`: delete legacy handlers only after tests confirm no live navigation, form, assistant, or external link depends on them.
- `document only`: keep the current state when risk is higher than cleanup value.

### D2. Define AR Object Model

Decision needed:

- define entities and fields for expected income, issued CFDI, linked income, collected amount, outstanding balance, due date, payer, project/tournament, and status.

Output:

- `docs/product/ar-cfdi-income-model.md`

Decision for now:

- AR S1 is read-only and treats collection/cash state as `unknown` until a later matching/conciliation spec validates a canonical collection source.
- F2-D1 keeps `bank_movements` as candidate evidence only; treasury/cash-flow routes do not become AR collection authority until a binding match policy is approved.

Implementation:

- `src/samchat/ar/service.py`
- `build_ar_read_model(...)`
- `GET /admin/finanzas/cuentas-por-cobrar`
- `src/samchat/ar/admin_ui.py`

The admin route renders AR S1 from the canonical read model and does not
duplicate AR source queries.

F2 S3 pre-matching consumer:

- `src/samchat/ar/matching.py`
- `build_ar_matching_workbench(...)`
- `GET /admin/finanzas/cuentas-por-cobrar`
- Reads `bank_movements` only as candidate evidence.
- Does not accept matches, update bank reconciliation, or establish collection
  authority.

D2 collection authority decision:

- Future AR collection proof must come from a dedicated accepted-match authority
  such as `ar_collection_matches`.
- `bank_movements.conciliacion_estado` is not AR collection authority by itself.
- First implementation must prohibit split payments, partial payments,
  overpayments, and many-to-one or one-to-many collection matches.
- Cashflow/planning can consume accepted AR collection matches, not pre-matching
  candidates.

F2 S4 implementation:

- `src/samchat/ar/collection_matches.py`
- `ar_collection_matches`
- `ar_collection_match_audit_log`
- `POST /admin/finanzas/cuentas-por-cobrar/matches/accept`
- `POST /admin/finanzas/cuentas-por-cobrar/matches/{match_id}/reverse`
- Active accepted matches can move AR rows to `matched_collected`.
- The implementation does not write to `bank_movements.conciliacion_estado`.

### D3. Define Cashflow Read Model

F3 S1 implementation:

- `src/samchat/cashflow/service.py`
- `build_cashflow_planning_read_model(...)`
- `tests/unit/test_cashflow_planning_read_model.py`

The projection separates actual bank cash, approved obligations, budget monthly
plan, recognized income, accepted AR collection matches, expected uncollected
income, and forecast. It is read-only and does not consume AR pre-matching
candidates as collected cash.

F3 S2 admin consumer:

- `GET /admin/finanzas/cashflow`
- `src/samchat/cashflow/admin_ui.py`
- server-rendered read-only Finance Spine view over
  `build_cashflow_planning_read_model(...)`.
- Does not use `/admin/contabilidad/cash-flow` as source authority.

### D4. Decide Assistant Finance Tool Contract

F4 D1-D2 decision and S1-S3 implementation:

- `docs/product/assistant-finance-source-contract.md`
- assistant finance answers must read from canonical Finance Spine read models;
- AR answers use AR read models, cashflow answers use cashflow planning read
  model, and legacy `/admin/contabilidad/cash-flow` is not source authority;
- `run_finance_read_adapter(...)` exposes read-only adapter intents for
  `ar.summary`, `ar.prematching`, and `cashflow.summary`;
- `assistant_finance_read` exposes those intents as a read-only assistant tool;
- `render_finance_read_answer(...)` renders those finance answers
  deterministically so `candidate_match` is never treated as cobranza AR
  probada and `forecast_net` stays labeled as derived;
- any future write-like assistant action still requires preview, authority,
  confirmation, and receipt.

### D5. Decide Legacy Route Policy

Decision needed:

- choose one policy for old presupuesto handlers: document only, hide from navigation, redirect, or remove in a later approved cleanup.

## Validation Plan

```bash
rg -n "Canonical Finance Map|AR depends on|Cashflow depends on|presupuestos-legacy|admin_budget_routes|cfdi_income_bridge" docs/product
git diff -- docs/product/samchat-product-spine.md docs/product/finance-spine-map.md
git status --short
```
