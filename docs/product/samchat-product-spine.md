# SamChat Product Spine

Status: Draft v0.1
Date: 2026-08-20
Scope: roadmap consolidation and product authority map only. No runtime, schema, route, or permission changes.

## Authority

SamChat has multiple planning documents and runtime surfaces. They are not equal.

The live `sam.chat` product spine is:

- `copa_telmex_dashboard.py`, which mounts the production web application and the assistant router.
- `src/devnous/gastos/`, which owns the active gastos, finance, operations, admin, support, and user surfaces.
- `src/samchat/assistant/`, which owns the governed assistant, RAG, routing, tools, traces, and assistant-admin APIs.
- `src/samchat/finance_platform/`, `src/samchat/budgets/`, and `src/samchat/sam_inbox/`, which provide newer finance, budget, and operational read-model projections.

Secondary or historical planning material includes:

- `docs/deployment/DevNous_Implementation_Roadmap.md`, which explicitly says it is DevNous-centered planning material and not the current production source of truth for live `sam.chat`.
- `INTEGRATION_PLANS_SUMMARY.md` and `ZAUBERN_MEMORY_AGENT_INTEGRATION_PLAN.md`, which are integration plans, not current runtime authority.
- Broad MCP/platform README sections that describe separate launcher/API surfaces rather than the current production web app.

Rule: a roadmap item does not become product authority until it is tied to a live route, service, model, test, artifact, or approved implementation work order.

## Product Spine Governance

This document is the SSOT for the SamChat roadmap/product authority map. It is
not the global technical SSOT for the repository, and it does not override
verified runtime behavior.

Precedence rules:

1. Verified runtime behavior wins over roadmap claims.
2. Repo-level SSOT documents approved in a separate governance story win over
   secondary planning docs.
3. This Product Spine wins over secondary plans for roadmap consolidation,
   prioritization, and product authority mapping.
4. Closeout artifacts are historical evidence, not runtime authority.

Valid roadmap statuses:

- `live`: connected to current product behavior through concrete evidence.
- `planned`: accepted direction with no runtime connection yet.
- `coded_not_wired`: implementation exists but is not connected to a live route,
  UI, tool, permission, export, or owning workflow.
- `obsolete_or_secondary`: useful historical or secondary reference, but not a
  current product lane.
- `legacy/reference`: older behavior or material kept for comparison or
  migration only.
- `not runtime-connected`: artifact or code exists, but no live product contract
  makes it usable by operators or the assistant.

Minimum evidence for `live` status is at least one concrete product connection:
route, service, model, export, UI entrypoint, permission, test,
deployment/runtime validation, or approved work order that binds the item to the
owning product surface.

A secondary roadmap, integration plan, artifact closeout, or memory note can
inform a future story. It cannot by itself mark an item `live`, authorize a
mutation, or supersede the owning runtime module.

## Roadmap Lanes

### Core Operations

This lane covers tournaments, registration, OCR, documentos, gastos, approvals, users, roles, notifications, media, and operating workflows. It is the operational foundation under `src/devnous/gastos/` and the production app entrypoint.

Current authority evidence:

- `copa_telmex_dashboard.py` mounts `assistant_router` and the admin/user/support/operations routers.
- `docs/oferta_plataforma_resumen_ejecutivo_cliente_2026-06-23.md` identifies registration, OCR, gastos, CFDI, admin control, communications, and media as already-built platform value.

### Finance Spine

This lane covers finance command center, payment run, COI, DIOT, CFDI matching, budgets, income budgets, CFDI income links, accounts receivable, cash control, forecast, and planning.

Current authority evidence:

- `/admin/finanzas` is implemented in `src/devnous/gastos/routes/admin_routes.py`.
- `/admin/finanzas/payment-run` and related payment-run routes are implemented in `src/devnous/gastos/routes/admin_routes.py`.
- `/admin/presupuestos` and tournament budget detail routes are registered from `src/devnous/gastos/routes/admin_budget_routes.py`.
- `src/samchat/finance_platform/service.py` builds read-only snapshots for action queue, cash control, accounting close, tax readiness, payment run, copilot prompts, and finance brief.
- `src/samchat/budgets/service.py` owns canonical budget versions, lines, concepts, monthly plan, actuals, income lines, and SSOT budget catalog handling.
- `docs/product/presupuestos-route-policy.md` classifies Presupuestos route
  ownership, bridge handlers, and legacy references for cleanup planning.

### Artifact Layer

This lane separates product artifacts from evidence artifacts.

Product/runtime artifacts:

- `assistant_artifacts` in `src/devnous/gastos/models.py`.
- `assistant_save_artifact` in the assistant tool surface.
- Report exports, finance workbooks, budget income workbooks, and expediente snapshots.

Evidence/closeout artifacts:

- `artifacts/*.md`
- `artifacts/*.json`
- client-facing review docs under `docs/oferta_*`

Rule: an artifact is not product-connected merely because a markdown closeout exists. Connected artifacts need a route, model, permission, export, UI entrypoint, or assistant tool contract.

### Assistant Copilot

The assistant is a governed copilot over existing product lanes, not the primary product container.

Current authority evidence:

- `docs/assistant/rqf-assistant-009-quality-roadmap.md` sets canary posture to read-only and writes disabled.
- `src/samchat/assistant/router.py` defines read and write tool groups, routing, RAG endpoints, export/report endpoints, admin assistant routes, and governed tool execution.
- Prior assistant work keeps proposal, preview, confirmation, receipt, and explicit authority boundaries.

Rules:

- The assistant should consult live modules before inventing business state.
- The assistant may summarize, retrieve, draft, preview, export, or propose.
- The assistant must not execute effects without the relevant authority surface and confirmation.
- The assistant should not replace Finance, Presupuestos, Sam Inbox, or tournament operations UI.

### Secondary DevNous/MCP

DevNous API, MCP launcher, memory-orchestrator plans, and production-readiness-agent plans remain useful references. They are not the active roadmap for live `sam.chat` unless a future approved spec promotes a bounded piece into the live product spine.

## Inventory

| lane | item | status | evidence | owner surface | next decision |
|---|---|---:|---|---|---|
| Core Operations | Production web app | live | `copa_telmex_dashboard.py`; README runtime status | `copa_telmex_dashboard.py` | Keep as top-level runtime authority. |
| Core Operations | Gastos/documentos/admin modules | live | `src/devnous/gastos/` | `src/devnous/gastos/routes/` and services | Treat as canonical for current operations. |
| Core Operations | Registration/OCR/document review | live | `copa_telmex_dashboard.py`; `docs/CTT_OCR_PIPELINE.md`; oferta summary | dashboard + registration review routes | Inventory remaining contractual closures separately. |
| Finance Spine | Finance command center | live | `/admin/finanzas` in `admin_routes.py`; `finance_platform` service | `src/samchat/finance_platform/` + admin route | Keep as finance operations landing surface. |
| Finance Spine | Payment run | live | `/admin/finanzas/payment-run` routes in `admin_routes.py` | admin finance routes + payment services | Fold into cashflow roadmap as near-term operational cash surface. |
| Finance Spine | COI/DIOT/tax readiness | live | `finance_platform` service; COI/DIOT actions in admin routes | finance command center | Keep as accounting-close surface. |
| Finance Spine | Presupuestos canonical routes | live | `register_presupuestos_routes(router)` and `admin_budget_routes.py` | `src/devnous/gastos/routes/admin_budget_routes.py`; `src/samchat/budgets/` | Treat as canonical budget surface. |
| Finance Spine | Presupuestos legacy routes | obsolete_or_secondary | legacy `/admin/presupuestos-legacy` and older budget handlers in `admin_routes.py` | `src/devnous/gastos/routes/admin_routes.py` | Do not extend until canonical/legacy boundary is cleaned. |
| Finance Spine | CFDI income links | live | `cfdi_income_bridge_service`; budget income import/export/link routes | budget routes + service | Use as base for AR/real-income reconciliation. |
| Finance Spine | Accounts receivable / CFDI AR | planned | oferta docs list `cuentas por cobrar con emisión CFDI final` as closure item; user mentioned AR roadmap | Finance Spine | Define AR from existing CFDI income and payment/cash models before building. |
| Finance Spine | Cashflow / planning / forecast | coded_not_wired | `cash_control_center`, `finance_brief`, budget monthly plan/actuals; oferta docs mention forecast as additional value | finance platform + budgets | Consolidate into one cashflow planning read model and UI path. |
| Artifact Layer | Closeout markdown artifacts | live | `artifacts/*.md` | documentation/evidence | Keep as historical evidence, not runtime feature surface. |
| Artifact Layer | Assistant saved artifacts | live | `assistant_artifacts` model; `assistant_save_artifact` tool | assistant router + assistant DB models | Decide what artifact types need first-class UI/export. |
| Artifact Layer | Expedition/tournament snapshots | live | assistant admin tournament snapshot routes | assistant router | Keep as read models for operations and owner-folder workflows. |
| Artifact Layer | Sponsor/marketing proof package builders | planned | oferta marketing docs and closeout artifacts | marketing/sponsor lane | Require separate story before implementation. |
| Assistant Copilot | Assistant chat/RAG | live | `/assistant`; `/api/assistant/rag/*`; assistant router | `src/samchat/assistant/` | Keep under read-only/governed posture unless authority is explicit. |
| Assistant Copilot | Finance assistant tools | live | `FINANCE_READ_TOOLS`, `FINANCE_WRITE_TOOLS`, finance tool dispatch | assistant router/tools | Keep assistant as adapter over finance modules, not source of truth. |
| Assistant Copilot | Claude-like owner/operator workflow | coded_not_wired | owner-needs docs, assistant roadmap, closeout artifacts | assistant copilot + artifact layer | Promote only bounded read-only or preview-first slices. |
| Secondary DevNous/MCP | DevNous implementation roadmap | obsolete_or_secondary | roadmap says not current production source of truth | docs/deployment | Use as reference only. |
| Secondary DevNous/MCP | MCP launcher/platform claims | obsolete_or_secondary | README separates MCP launcher from production web server | `mcp_platform_launcher.py` | Do not use as live product authority without separate validation. |
| Secondary DevNous/MCP | Zaubern memory orchestrator plan | planned | `ZAUBERN_MEMORY_AGENT_INTEGRATION_PLAN.md` | integration planning | Separate from SamChat product spine unless approved. |

## Finance Consolidation Backlog

### F1. Canonical Finance Map

Goal: make one map of finance surfaces and their authority.

Output:

- `docs/product/finance-spine-map.md`

Inputs:

- `/admin/finanzas`
- `/admin/finanzas/payment-run`
- `/admin/presupuestos`
- budget versions, lines, concepts, monthly plan, actuals
- CFDI income links
- finance platform snapshot
- Sam Inbox finance items

Acceptance:

- Every finance dashboard card points to a canonical route/service.
- Legacy budget handlers are marked as legacy or removed from the roadmap.
- AR/cashflow items are not mixed with AP/payment-run items without labels.

Next checkpoint:

- D1 freezes `admin_budget_routes.py` as the canonical presupuesto owner and keeps legacy presupuesto handlers under a `document only` policy until a separate route cleanup is approved.

### C2. Presupuestos Legacy Route Inventory And Policy

Goal: make the Presupuestos route cleanup decision explicit before future AR,
cashflow, planning, assistant finance export execution, or archive work extends
the wrong route owner.

Status: policy/inventory only. No route removal, redirect, hide, permission,
DB, import/export, or runtime behavior change.

Output:

- `docs/product/presupuestos-route-policy.md`

Acceptance:

- Canonical Presupuestos routes are classified as `canonical_owner`.
- Remaining `/admin/presupuestos/*` handlers in `admin_routes.py` are
  classified as either `bridge_required_by_canonical_ui` or
  `bridge_external_dependency` or `candidate_remove_later` based on C2b/C2c
  route-target and export-catalog evidence.
- `/admin/presupuestos-legacy` is classified as `legacy_reference`.
- Future cleanup options are limited to `hide`, `redirect`, `remove later`, or
  continued `document only`.
- Bridge and legacy routes are explicitly non-authority for new AR, cashflow,
  planning, or assistant finance work.

### F2. Accounts Receivable And Real Income

Goal: define AR as a product slice over income budgets, PSP CFDI income, customer receivables, and cash collection state.

Output:

- `docs/product/ar-cfdi-income-model.md`
- `src/samchat/ar/service.py`

Inputs:

- `budget_lines` with `budget_direction=income`
- `budget_cfdi_income_links`
- PSP CFDI income candidates
- user-facing receivable preview surfaces

Acceptance:

- AR has a clear object model: expected income, issued CFDI, linked cash/income, outstanding balance, due horizon.
- It does not reuse AP/payment-run semantics silently.
- It defines what is read-only versus what can mutate.

S1 decision:

- AR S1 does not block on bank matching. Collection remains `collection_unknown` unless a later matching/conciliation spec establishes collection proof.

D2 decision:

- Future AR collection proof requires a dedicated accepted-match authority such
  as `ar_collection_matches`.
- Treasury/accounting reconciliation status is not enough to prove AR
  collection.

S4 implementation:

- `ar_collection_matches` is now the accepted-match authority for AR collection
  proof. It remains separate from treasury/accounting reconciliation state.

### F3. Cashflow And Planning

Goal: consolidate cash pressure, approved unpaid documents, income actuals, budget monthly plan, run-rate, and forecast into one planning read model.

Inputs:

- `cash_control_center`
- payment run
- monthly budget plan and actuals
- finance brief
- executive dashboard/alerts where relevant

Acceptance:

- Cashflow separates actual cash, approved obligations, planned budget, expected income, and forecast.
- It produces one executive summary and one operations action queue.
- Assistant queries read from the same projection instead of recomputing loosely.

S1 implementation:

- `src/samchat/cashflow/service.py`
- `build_cashflow_planning_read_model(...)`
- read-only service; no UI, export, assistant tool, or mutation yet.

S2 implementation:

- `GET /admin/finanzas/cashflow`
- `src/samchat/cashflow/admin_ui.py`
- read-only admin view wired to the canonical cashflow planning read model.

### F4. Assistant Finance Copilot

Goal: expose the finance spine through assistant prompts, previews, and exports after the canonical read models exist.

Status: read-only/guidance complete. F4 does not authorize writes, direct export
execution, or managed export archiving.

Acceptance:

- Assistant answers cite the finance read model used through adapter source
  metadata and rendered source notes.
- Assistant finance source authority is defined in
  `docs/product/assistant-finance-source-contract.md`.
- F4 D1 defines source authority.
- F4 S1 implements `run_finance_read_adapter(...)` as a read-only adapter.
- F4 S2 exposes `assistant_finance_read` as a read-only assistant finance tool.
- F4 S3 renders `assistant_finance_read` responses deterministically through
  `render_finance_read_answer(...)`.
- Write-like finance actions require preview, authority, confirmation, and receipt.
- The assistant cannot create a parallel finance state.

Implemented intents:

- `ar.summary`
- `ar.prematching`
- `cashflow.summary`
- `budget.snapshot`
- `finance.platform`
- `finance.exports` guidance

Future stories only:

- direct export execution or archive from chat;
- any write-like finance action.

## Artifact Consolidation Backlog

### A1. Artifact Taxonomy

Define artifact classes:

- evidence closeout
- runtime saved artifact
- report/export
- expediente snapshot
- sponsor/marketing proof package
- assistant proposal/preview

Status: approved taxonomy in `docs/product/artifact-taxonomy.md`.

Acceptance:

- Each class has storage, ownership, permission, and lifecycle rules.
- Markdown closeouts are not treated as runtime objects.
- `assistant_artifacts` does not replace exports, expediente snapshots, sponsor
  packages, or budget source artifacts.

### A2. Runtime Artifact Index

Goal: list current runtime artifacts and whether they have UI, export, route, DB model, tests, and authority.

Status: initial index in `docs/product/runtime-artifact-index.md`; A4 adds
read-only admin discoverability through `/admin/artifacts`.

Acceptance:

- `assistant_artifacts` entries have a discoverable owner surface.
- Finance and budget exports are listed separately from assistant artifacts.
- Proposed sponsor/marketing artifacts remain planned until approved.
- `/admin/artifacts` does not execute exports, expose artifact content, archive
  files, or create a cross-product artifact center.

### A3. Artifact-To-Assistant Boundary

Goal: let the assistant retrieve and draft artifacts without becoming the artifact system.

Status: boundary documented in `docs/product/assistant-artifact-boundary.md`.

Acceptance:

- Assistant-generated artifacts are drafts until confirmed.
- Durable artifacts require a route/model/export contract.
- Sponsor/client-facing packages require human approval.

### A5. Owner/Operator Assistant Preview Consolidation

Goal: classify the Claude-like owner/operator workflow as a live preview
surface where it is actually wired, while keeping durable owner-pack artifacts,
exports, notifications, and writes out of scope.

Status: implemented as assistant preview/read-only surfaces. The conversation
path exposes `owner.operator_workflow.preview`; the assistant tool registry
exposes `assistant_owner_entity_folder_workspace` as `preview_only`.

Acceptance:

- Owner/operator workflow outputs are `assistant_proposal_preview` traces, not
  `runtime_saved_artifact` rows.
- `owner.operator_workflow.preview` and
  `assistant_owner_entity_folder_workspace` remain non-authoritative:
  `writes_attempted == 0`, no side effects, no export, no folder archive, no
  notification, and no owner-pack state write.
- Code that exists but is not exposed through a conversation path, route, tool,
  permission, UI, or export remains labeled as internal/reference or
  `available_not_wired`.
- Durable owner-pack folders, exports, publication, archive/delete lifecycle,
  and sponsor/client delivery require a future story/spec.

## Assistant Boundary

The Assistant Boundary section is an executive index. The detailed source of
truth lives in:

- `docs/product/assistant-artifact-boundary.md` for artifact drafting, saved
  artifacts, exports, snapshots, and sponsor/client-facing packages.
- `docs/product/assistant-finance-source-contract.md` for finance read
  authority, source metadata, deterministic answers, and pending finance
  intents.
- `docs/product/artifact-taxonomy.md` for artifact classes, owners,
  permissions, and lifecycle rules.
- `docs/product/runtime-artifact-index.md` for current versus planned runtime
  artifact connections.

The assistant may:

- read authorized product canon, RAG, SQL/read models, and runtime artifacts;
- cite the source surface and evidence it used;
- summarize, compare, draft, preview, export, or propose;
- route users to the owning live module;
- save approved reusable artifacts only when the governing tool, route, model,
  permission, and lifecycle contract permit it.

The assistant must not:

- treat roadmaps, docs, closeout artifacts, or memory as live operational facts
  without runtime evidence from the owning module;
- write finance, budget, tournament, sponsor, expediente, artifact, or external
  effect state without the relevant authority path, confirmation, permission,
  receipt, and module-owned mutation surface;
- recognize cobranza, change presupuestos, authorize tournaments, issue sponsor
  proof packages, modify expedientes, or execute external effects from chat
  alone;
- claim a planned integration is complete;
- describe coded-but-unwired artifacts as live. They must be labeled `planned`,
  `legacy/reference`, or `not runtime-connected` until a route, model,
  permission, UI entrypoint, export, or assistant tool contract connects them;
- bypass module permissions or replace first-class UI for Finance,
  Presupuestos, Sam Inbox, tournament operations, or expediente workflows.

## Validation Plan

Read-only validation for this document:

```bash
rg -n "Product Spine|Finance Spine|Assistant Boundary|obsolete_or_secondary" docs/product/samchat-product-spine.md
git diff -- docs/product/samchat-product-spine.md
```

Future implementation validation should add tests only when code or route behavior changes. This document alone does not require runtime tests.

## Decision Backlog

| id | lane | question | type | priority | next checkpoint | notes |
|---|---|---|---|---:|---|---|
| OQ-001 | Core Operations | Should `docs/product/samchat-product-spine.md` become the replacement for the missing `SAMCHAT_SSOT.md`, or remain a product document under `docs/product/`? | decision | P1 | story | Do not rename or promote until an explicit SSOT governance story is approved. |
| OQ-002 | Finance Spine | Which finance lane should be delivered next after AR, cashflow, and finance assistant export guidance: direct export execution/archive or legacy presupuesto cleanup? | decision | P1 | story | Avoid mixing export execution or archiving with route cleanup in one scope. |
| OQ-003 | Finance Spine | Should legacy presupuesto handlers be removed, hidden, or left as `obsolete_or_secondary` documentation references? | inventory_ready | P2 | story | C2 adds route policy/inventory. C3 must choose one action: `hide`, `redirect`, `remove later`, or keep `document only`. |
| OQ-004 | Assistant Copilot | Should any additional budget assistant read beyond `budget.snapshot` be added? | defer | P3 | research | `budget.snapshot` is implemented in F4 S4; new budget reads need separate scope. |
| OQ-005 | Assistant Copilot | Should any additional Finance Platform assistant read beyond `finance.platform` be added? | defer | P3 | research | `finance.platform` is implemented in F4 S5; new platform reads need separate scope. |
| OQ-006 | Assistant Copilot | Should finance exports be executable or archived directly from chat after guidance is implemented? | spec_needed | P2 | story | F4 S6 implements guidance only; export authority stays with the owning product surface. |
| OQ-007 | Artifact Layer | Should assistant artifacts receive a first-class admin UI, or remain conversation-scoped for now? | decision | P2 | story | Must preserve the Assistant Artifact Boundary either way. |
| OQ-008 | Artifact Layer | Which sponsor/marketing proof artifacts are contractual and which are optional add-ons? | research | P2 | story | Sponsor/client-facing packages require human approval and a separate artifact story. |
| OQ-009 | Assistant Copilot | What bounded slice, if any, should promote the coded-not-wired owner/operator assistant workflow? | resolved_for_preview | P3 | validation | A5 classifies the wired path as preview-only: conversation `owner.operator_workflow.preview` plus tool `assistant_owner_entity_folder_workspace`. Future durable folders/exports need separate approval. |
| OQ-010 | Secondary DevNous/MCP | Which Secondary DevNous/MCP components deserve promotion into the live SamChat product spine? | defer | P3 | research | Reference-only until a bounded promotion story is approved. |
