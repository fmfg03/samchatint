# SamChat Engineering Canon for ChatGPT

Version: 2026-09-10

Repository: `fmfg03/samchatint`

Repository baseline inspected: `main` at `7684713cc2e529b8f48768553cd1acc6f7e1e39b`

Purpose: reason about SamChat as an engineering system. This is not a product pitch, a deployment receipt, or a substitute for inspecting the target code before a change.

## 1. Primary rule

SamChat is one repository with several non-equivalent runtimes, business domains, persistence patterns, and maturity levels.

Never reason about “the SamChat app,” “the SamChat database,” or “the SamChat auth system” without naming the target surface.

## 2. Evidence levels

Keep these levels separate:

1. `proposal`: promised or described in the 2025 SOW.
2. `planned`: accepted direction without connected runtime evidence.
3. `coded_not_wired`: code exists but has no live route, tool, UI, permission, or owning workflow.
4. `repo_live`: connected in current `main` through code plus route/tool/UI/test evidence.
5. `deployed_verified`: observed in the active production release with health and workflow evidence.
6. `business_accepted`: validated by the owning user group through UAT or explicit acceptance.

Code in `main` is not automatically deployed. Deployed code is not automatically accepted. A proposal is never implementation evidence.

## 3. Canonical runtime order

When the user does not name a runtime, use this order:

1. Live `sam.chat` web work: `copa_telmex_dashboard.py`.
2. Gastos, finance, operations, admin, users, support, and webhooks: `src/devnous/gastos/` as mounted by the live web runtime.
3. Governed assistant: `src/samchat/assistant/`, mounted by the live web runtime.
4. Tournament registration and review: dashboard routes plus `src/devnous/copa_telmex/`, `src/devnous/tournaments/`, and `src/samchat/tournaments_v2/` as applicable.
5. Generic DevNous API: `src/devnous/api.py`.
6. CLI: `src/samchat/main.py`.
7. MCP platform/demo: `mcp_platform_launcher.py`.
8. Nested applications such as `copatelmex/`, `goal-fest-page/`, and `samchat-mvp/`: separate scope.

The production service declared by the repository remains `samchat-gastos.service`, launching:

```text
uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000
```

## 4. Current live-web composition

`copa_telmex_dashboard.py` is a composite FastAPI application. Current repository evidence shows it mounts:

- session middleware;
- auth routes;
- admin routes;
- user routes;
- support routes;
- webhook ingress under `/ingress`;
- the assistant router;
- health and readiness endpoints;
- registration review, team, and player operations.

Default consequence: changes to this runtime can affect real financial, authorization, tournament, or evidence workflows.

## 5. Canonical product lanes in current `main`

| Lane | Canonical owner | Current repository status |
| --- | --- | --- |
| Documentos, gastos, approvals, payments | `src/devnous/gastos/` | `repo_live` |
| Registration and OCR review | `copa_telmex_dashboard.py`, `src/devnous/copa_telmex/`, OCR services | `repo_live` |
| Governed assistant | `src/samchat/assistant/` | `repo_live`, predominantly read-only/proposal-first |
| Finance command center | `src/samchat/finance_platform/` plus admin routes | `repo_live` |
| Presupuestos | `src/samchat/budgets/` plus `admin_budget_routes.py` | `repo_live` |
| Accounts receivable | `src/samchat/ar/` plus `/admin/finanzas/cuentas-por-cobrar` | `repo_live`; business UAT still unproven |
| Cashflow planning | `src/samchat/cashflow/` plus `/admin/finanzas/cashflow` | `repo_live`; read-model/UI |
| Payment Run | payment services plus `/admin/finanzas/payment-run` | `repo_live`; business UAT still unproven |
| Sam Inbox | `src/samchat/sam_inbox/` plus `/admin/sam-inbox` | `repo_live`; read-only projection |
| Runtime artifact index | `src/samchat/artifacts/` plus `/admin/artifacts` | `repo_live`; discoverability only |
| Sports/owner projections | `src/samchat/sports_platform/` and assistant owner-pack modules | mixed: connected read models, incomplete source coverage |
| Sponsor approval/proof workflow | `src/samchat/sports_platform/sponsor_media.py` | model/projection exists; persistent end-to-end workflow is not proven |

## 6. Data boundaries

SamChat is hybrid.

### Direct Postgres paths

Strong signals include:

- `DATABASE_URL` in the live dashboard;
- `POSTGRESQL_URL` in DevNous configuration;
- SQLAlchemy models and sessions in gastos, assistant, finance, budgets, AR, cashflow, accounting, payroll, and registration review;
- direct SQL used by operational services such as Payment Run.

### Supabase-dependent paths

Strong signals include:

- `src/samchat/tournaments_v2/supabase_client.py`;
- `src/devnous/tournaments/core/supabase_sync.py`;
- assistant bridge/query paths that still consume Supabase semantics;
- nested applications with Supabase auth, functions, or migrations.

### Required behavior

Before changing schema, reads, writes, or auth:

1. name the runtime;
2. name the owning domain;
3. identify direct Postgres, Supabase, or hybrid behavior;
4. identify the canonical write owner;
5. identify migration and rollback requirements.

Do not create a new persistence path when a canonical service already owns the workflow.

## 7. Auth and authority boundaries

The live runtime combines several identity mechanisms; they are not interchangeable:

- internal signed session cookie and `empleados` identity;
- role and permission checks for admin, finance, accounting, operations, and superadmin functions;
- a Supabase-to-local assistant auth bridge;
- Telegram authorization through `telegram_user_id`;
- explicit employee/position configuration for selected payment and approval paths.

High-risk actions require more than a visible button or tool definition. Verify:

- current employee identity;
- role and effective permissions;
- project/position-specific authorization route;
- beneficiary constraints;
- explicit confirmation when the assistant is involved;
- idempotency and an audit receipt.

### 7.1 Direction executive boards and reports

Date: 2026-09-11

Reason: this amendment governs the pending correction to #314 and #315 from a
customer-role interpretation to an internal Direction authority surface. The primary routes are
`/direccion/tableros` and `/direccion/reportes`; temporary legacy GET
redirects do not retain a customer-facing authorization or write API.

The non-superadmin authorization contract is position-scoped, not role-scoped:

1. the session resolves to an active internal `empleado`;
2. the employee has an active assignment to one of
   `direccion_general`, `direccion_administracion_finanzas`,
   `direccion_goat`, or `director_operaciones`;
3. that position is actively mapped to a portfolio, and the requested
   tournament/report belongs to that active assigned scope.

`superadmin` has supervision over active portfolios. `admin` does not receive
global access merely by role. The `cliente` role is neither a prerequisite nor
an authorization concept for these surfaces. Specific permissions may restrict
actions within the surface, but never create portfolio or tournament scope or
substitute the eligible position. There is no global fallback for
position-scoped employees.

Executive board and published-report consumption are read-only within assigned
scope. Report schedule configuration, draft creation and state transitions are
internal governed writes requiring their specific authority and audited actor.
Financial, CxC, payment, cashflow, and operational-detail domains remain
unavailable unless a separate canonical permission authorizes them.

The physical `client_*` tables, package names, and route-module filenames are
temporary compatibility debt. Do not rename them in this correction; a
separate migration, compatibility inventory, rollback plan, and approval are
required.

Evidence and temporary discrepancy: production remains at
`81335f8ddd7e103839719f62235a14d11bb2bd47` with the incorrect client-based
interpretation. Route/service changes and focused tests exist only as an
uncommitted diff in an isolated clean worktree, with 26 focused tests and
route-contract validation passing. The change becomes `repo_live` only after
merge, and `deployed_verified` only after a new release plus authenticated
smoke validation. It remains not `business_accepted` until UAT and real scope
configuration are complete.

## 8. Canonical financial workflow invariants

### 8.1 Document states are business state

`Documento.estado` is not presentation metadata. It drives queues, permissions, Payment Run eligibility, rejection behavior, and history.

Do not infer a payment status from a fallback label. Derive it from the canonical document state, payment evidence, and cutoff membership.

### 8.2 Budget control occurs once for approved expense-report reimbursements

Business rule:

- an `INFORME` requiring budget classification must receive its budget concept before final approval;
- a reimbursement `SOLICITUD` derived from that approved, classified `INFORME` inherits the budget concept and approver;
- the derived reimbursement must not repeat Control Presupuestal;
- it can enter Programación de Pagos only when the linked `INFORME` is approved and actually has a budget concept.

Repository status at this baseline:

- PR #303 is merged and recovers eligible reimbursements stuck in `control_presupuestal`;
- PR #306 is merged and prevents ineligible requests from appearing as falsely `programada` in payment history;
- PR #304 merged and deployed as `dcbeea8423030e0123101a35378d765ea3c96f32`;
  it adds the explicit guard that the approved linked `INFORME` must itself have
  `budget_concept_id` before auto-promotion.

Do not describe the safety invariant as business-closed until the historical
reconciliation, affected-reference verification, and Finance UAT are evidenced.

### 8.3 Payment Run

Canonical states visible to Payment Run are:

- `aprobado` and unpaid: eligible to schedule/close;
- `en_proceso_pago`: inside a closed operational cutoff and awaiting payment proof;
- `pagado` or `pagado_en` present: paid history.

Requests in `borrador`, `enviado`, `control_presupuestal`, or `rechazado` are not scheduled payments.

Closing a Payment Run is an operational cutoff. It does not by itself prove that money moved. Payment proof/confirmation is a separate authority step.

### 8.4 Accounts receivable

AR is not the inverse of AP and must not reuse Payment Run semantics.

Canonical AR separation:

- expected income;
- issued CFDI;
- approval of the income/CFDI link;
- accepted collection match;
- accounting entry for CxC;
- accounting entry for collection;
- reversal with audit trail.

Candidate bank movements do not prove collection. `ar_collection_matches` is the accepted-match authority in current code.

### 8.5 Cashflow

Cashflow must keep separate:

- actual bank cash;
- approved AP obligations;
- budget plan;
- issued or expected income;
- collection proven through accepted AR matching;
- derived forecast.

Never label forecast or a collection candidate as actual cash.

## 9. Assistant engineering contract

The product model follows a governed Claude Code-style work loop:

1. understand the business objective;
2. load relevant current context;
3. create or resume a case when continuity matters;
4. select candidate tools using semantic metadata;
5. execute reads under policy;
6. test answer sufficiency;
7. render evidence, gaps, and proposed actions;
8. show a preview/business diff for any intended write;
9. obtain explicit authority;
10. execute idempotently and return a receipt.

The assistant is the primary conversational work interface, but it is not a parallel source of financial or tournament truth.

Current safe default: read, investigate, compare, summarize, draft, preview, and export where a connected contract exists. Writes require the owning canonical action plus policy, authority, confirmation, and audit.

## 10. Tournament and OCR boundaries

Registration review is a governed intake path with session, asset, draft, reprocess, adjudication, and commit concepts.

Preserve these distinctions:

- generated OCR data;
- human-reviewed data;
- validated draft;
- committed team/player state;
- post-commit authority and incident evidence.

Minimum roster eligibility, duplicate-photo detection, tournament scope, and blocking incident policy must be recalculated at commit time. Do not insert a player with a blocking incident as eligible downstream.

External FMF verification is not proven merely because a rule, route name, or mock API exists.

## 11. Schema and migration rules

- Production web startup must not become an uncontrolled schema-owner process.
- Owner-run migrations must be explicit, idempotent where feasible, backed up, and verified before application deployment.
- Runtime `ensure_*_schema` helpers are not blanket authorization for new DDL.
- Backfills default to dry-run and must require explicit selectors, actor, and apply confirmation.
- Any repair that changes financial or authorization state needs a recovery snapshot or equivalent audit evidence.

## 12. CI and release truth

Current repository evidence contains two GitHub workflow files:

- `.github/workflows/assistant-scoped-gate.yml`;
- `.github/workflows/test.yml`.

There is no `.github/workflows/nightly.yml` at the inspected baseline. Therefore, a three-level Fast PR / Main / Nightly architecture is not current repo truth.

The assistant-scoped workflow currently runs on both pull requests and pushes to integrated branches. The Test Suite uses Python 3.11 and 3.12 matrices plus registration and accepted-regression guards. Some lint/security steps are non-blocking; do not describe them as strict gates without inspecting job conclusions and branch rules.

Production deployment is governed by one active systemd drop-in:

```text
/etc/systemd/system/samchat-gastos.service.d/50-current-release.conf
```

Release validation must confirm:

- exact deployed commit;
- one active drop-in;
- correct `WorkingDirectory`;
- release guards;
- `/healthz` and `/readyz`;
- no restart loop;
- a focused workflow smoke for the changed domain.

## 13. Source hierarchy

When sources disagree, use this order:

1. observed production runtime behavior and exact deployed commit;
2. runtime entrypoint and owning service code on that commit;
3. repository `AGENTS.md` and explicit SSOT documents;
4. routes, models, migrations, tests, and release guards;
5. accepted UAT evidence;
6. subsystem docs;
7. roadmap and closeout docs;
8. root README marketing sections;
9. SOW claims as scope evidence only.

## 14. Default answer protocol

For any SamChat question:

1. state the target runtime;
2. state the target domain;
3. state the data boundary;
4. state the evidence level: proposal, code, deployed, or accepted;
5. identify the canonical read and write owners;
6. state missing evidence;
7. only then recommend or implement.

## 15. Forbidden assumptions

Do not assume:

- `main` is deployed;
- a route proves a complete end-to-end workflow;
- a passing unit test proves production data correctness;
- the root README describes the live product accurately;
- all persistence is Postgres or all persistence is Supabase;
- assistant memory can override live data or policy;
- a closed Payment Run means paid;
- an AR candidate means collected;
- a generated sponsor package means external publication occurred;
- contractual KPI targets are measured outcomes;
- a code repair has corrected production records before deploy and reconciliation.

## 16. Safe short summary

SamChat is a multi-surface repository centered on the `copa_telmex_dashboard.py` FastAPI runtime under `samchat-gastos.service`. Its active product spine combines governed gastos/document workflows, Payment Run, budgets, AR, cashflow, Sam Inbox, tournament/OCR operations, and a read-mostly assistant. Persistence and identity are hybrid. Every answer must distinguish proposal, current repository code, verified deployment, and business acceptance. Financial and tournament writes must use canonical services with explicit authority, idempotency, audit, and evidence.

## 17. Final rule

When a clean product narrative conflicts with code, deployed behavior, or business evidence, preserve the conflict and state it explicitly.
