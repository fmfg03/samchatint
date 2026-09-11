# SamChat Codebase Sweep Report

Date: 2026-09-10

Repository: `fmfg03/samchatint`

Inspected baseline: `main` at `7684713cc2e529b8f48768553cd1acc6f7e1e39b`

Comparison baseline: prior sweep dated 2026-06-07

Conclusion: the June product-spine recommendation was implemented substantially. SamChat now has connected finance, inbox, artifact, assistant, AR, and cashflow surfaces. The primary risk has shifted from “what should we build first?” to “which repository capabilities are deployed, reconciled against production data, accepted by users, and contractually closed?”

## 1. Scope and method

This update used:

- current `main` source and Git history;
- runtime entrypoint and `AGENTS.md`;
- routes, services, models, migrations, tests, release guards, and product docs;
- PR status for #303, #304, and #306;
- the 2025 Plataforma Sports SOW as scope evidence;
- the June 2026 canon and sweep as comparison evidence.

No production server, database, secrets, or user session was accessed. No local test suite was executed because the cloned repository did not include its project virtual environment. GitHub reported successful `Assistant Scoped Gate` and `Test Suite` runs for the PR #306 head and for the current #304 head, but deployment and business UAT remain separate evidence levels.

## 2. Scale of change since the prior sweep

Repository observations:

- 748 commits reachable from current `main` carry commit dates after 2026-06-07;
- 266 `test_*.py` files exist;
- approximately 2,345 pytest test functions were detected textually;
- 15 dated/domain migration files after the June baseline are present by filename, including analyst cases, registration governance, beneficiary banking, loans, billing/collections approval, budget movement assignments, CFDI income month, and project authorization routes.

These counts indicate substantial change volume. They do not prove production deployment or acceptance.

## 3. Runtime finding

The primary live-web architecture remains consistent with the June sweep:

- production service: `samchat-gastos.service`;
- entrypoint: `copa_telmex_dashboard.py`;
- framework: FastAPI;
- primary operational backend: direct SQLAlchemy/Postgres paths;
- connected domains: gastos, auth, users, admin, support, webhooks, assistant, registration review, teams, and players.

Secondary runtimes remain separate:

- `src/devnous/api.py`;
- `src/samchat/main.py`;
- `mcp_platform_launcher.py`;
- Telegram launchers/adapters;
- nested applications.

## 4. Major delta from June: product spine

The June sweep recommended an internal Sam Inbox built on existing read models and canonical assistant actions. Current `main` now contains:

- `src/samchat/sam_inbox/service.py`;
- `/admin/sam-inbox`;
- role-aware filters and counts;
- finance, pending-payment, CFDI, operations, and direction projections;
- explicit read-only/stateless posture.

Result: Sam Inbox moved from recommendation to `repo_live` read-only product surface.

## 5. Major delta: finance spine

### 5.1 Finance command center

The finance platform now supplies canonical projections for:

- finance action queue;
- cash control;
- accounting close;
- tax readiness;
- Payment Run;
- finance copilot guidance;
- executive finance brief;
- Excel export.

### 5.2 Presupuestos

The budget layer expanded materially. Current evidence includes:

- canonical `/admin/presupuestos` routes;
- versions and lifecycle transitions;
- concepts and scoped catalog management;
- expense and income directions;
- tournament detail;
- monthly and weekly allocations;
- actuals and movement-level reconciliation;
- explicit movement-to-line assignments;
- Excel import/export;
- duplicate-concept reconciliation with dry-run and plan-hash controls;
- account mapping and budget/finance comparisons.

Legacy presupuesto handlers remain in `admin_routes.py`. New work should extend `admin_budget_routes.py` and `src/samchat/budgets/`, not the legacy surface.

### 5.3 Accounts receivable

The prior sweep described CxC as a partial or future lane. Current `main` contains:

- `src/samchat/ar/service.py` read model;
- operational and executive AR UI;
- `/admin/finanzas/cuentas-por-cobrar`;
- CFDI-income link decisions;
- billing schedule and actionable gaps;
- candidate bank-inflow matching;
- accepted collection match authority;
- reversal path and audit;
- accounting previews/entries;
- COI-ready XLSX export;
- assistant finance read integration.

Result: CxC is `repo_live`, but not yet `business_accepted`. The integral Finance UAT remains pending.

### 5.4 Cashflow

Current `main` contains:

- `src/samchat/cashflow/service.py`;
- a canonical planning read model;
- actual cash, AP obligations, AR, budget plan, and forecast separation;
- `/admin/finanzas/cashflow`;
- assistant finance adapter integration.

Result: cashflow moved from `coded_not_wired` to `repo_live` read model/UI.

### 5.5 Payment Run

Current evidence includes:

- access, management, and accounting-confirmation permission boundaries;
- scheduled-date management;
- operational cutoff closures;
- `en_proceso_pago` state;
- payment-proof upload and confirmation;
- history filtering;
- closure detail;
- payment-order XLSX export with snapshotted beneficiary/banking data;
- amount validation for reimbursements;
- accepted-regression protection.

Closing a cutoff does not register payment. Payment proof remains the separate completion authority.

## 6. Reimbursement incident update

Business invariant:

- the approved `INFORME` owns budget classification;
- a derived reimbursement request inherits that classification and bypasses a second Control Presupuestal gate;
- only an approved and classified linked `INFORME` can promote the reimbursement to Payment Run;
- an ineligible request must never appear as `programada` in payment history.

Repository status:

- #303 merged as `2f5a953d43c20921ad5e638e5a503383c851fcb2`: recovery of reimbursements stuck in `control_presupuestal`;
- #306 merged as current `main` `7684713cc2e529b8f48768553cd1acc6f7e1e39b`: defensive payment-history filtering and status classification;
- #304 is open at head `4e77ed12a810d4b37a28e67cad85314e56549138`: requires the linked approved `INFORME` to carry `budget_concept_id` before promotion.

Risk: #304 is behind current `main`. It must be updated, revalidated, merged, deployed, and followed by production reconciliation before the incident is closed.

## 7. Major delta: assistant

The assistant surface expanded from a tool/action layer into a governed work-loop architecture. Current modules include:

- work-frame classification;
- semantic tool registry;
- multi-candidate read-only execution;
- tool adjudication;
- answer sufficiency;
- evidence quality;
- analyst cases and persistence;
- case memory;
- document intake and confirmation loop;
- executive renderer and regression suite;
- owner/operator workflows;
- Owner Pack readiness and entity-folder workspace;
- finance read adapter;
- proposal/diff/receipt patterns;
- live canary definitions with write blocking.

Current boundary: the read/evidence loop is much stronger, but a general autonomous write-capable business cycle is not proven. The assistant must remain proposal/preview-first and use canonical business actions.

## 8. Major delta: tournament, sports, and Owner Pack

Current repository evidence includes:

- stronger governed OCR contracts and review stages;
- protected registration-review intake;
- minimum-roster and duplicate-photo integrity rules;
- human authority at draft, reprocess, and commit boundaries;
- SOUL draft, clone, coverage, and activation preview;
- sports platform projections;
- Director General/entity dossier projections;
- Owner Pack readiness, variable Q&A, and entity-folder workspace;
- sports operations status for assistant rendering.

The important remaining gaps are source coverage and transaction capability. Read-only projections can expose missing fields correctly, but they do not prove complete tournament operations, FMF integration, intelligent scheduling, logistics automation, or durable Owner Pack publication.

## 9. Major delta: artifacts and release governance

Current repository evidence includes:

- persisted assistant artifacts;
- finance, budget, AR, executive, and payment-order exports;
- runtime artifact discoverability through `/admin/artifacts`;
- explicit separation between runtime-saved artifacts, generated exports, closeout evidence, and planned artifacts;
- a release guard for accepted regressions;
- a registration operational-surface guard;
- one canonical systemd drop-in deployment pattern.

The artifact index is metadata/discoverability only. It does not read all artifact content or create a cross-product archive.

## 10. CI finding

Current `.github/workflows/` contains:

- `assistant-scoped-gate.yml`;
- `test.yml`.

No `nightly.yml` exists at the inspected commit. The assistant-scoped gate runs on both pull requests and pushes, so the previously described “Fast PR only / Main push / Nightly-RC” design is not present in current repo truth.

Additional caution:

- branch protection recently expected seven checks that were not registered under the visible current workflows;
- both visible PR workflows can pass while GitHub still blocks merge;
- branch protection required-check names need reconciliation with active workflow/job names.

## 11. Data and auth findings

The hybrid architecture remains real:

- direct Postgres owns most live-web financial and operational paths;
- Supabase-backed tournament and auth/query paths remain;
- browser session identity, local `empleados`, Supabase bridge identity, and Telegram identity are distinct;
- project/position authorization and beneficiary routing have become more explicit;
- named/default employee IDs still appear in selected payment controls and should be treated as configuration debt, not a universal role model.

## 12. SOW comparison update

Relative to the June 2026 review:

- gastos/AP: stronger and broader;
- budgets: materially stronger;
- CxC: moved from pending to substantial repository implementation;
- cashflow: moved to connected read model/UI;
- assistant/Owner Pack: materially stronger, still largely read-only;
- Payment Run: materially stronger, including exports and proof workflow;
- marketing/sponsor: projection and workflow-building blocks improved, but external publication and accepted proof-of-performance remain unproven;
- FMF, scheduling, logistics, live COI/SAE integration, and formal contractual handoff remain open or only partially evidenced.

## 13. Sacred artifacts and do-not-break list

Treat these as high-risk:

- internal auth, `empleados`, sessions, roles, access profiles, project positions;
- `documentos`, approvals, operational references, beneficiary routing;
- `expense_reports`, `cuentas_de_gastos`, reimbursements, returns, advances;
- Payment Run closures, closure items, payment proof, and paid state;
- budget versions, concepts, lines, monthly allocations, movement assignments;
- CFDI reports, income links, AR collection matches, reversals, accounting entries;
- COI/DIOT exports and accounting close;
- payroll and AMEX workflows;
- tournament registration sessions, assets, drafts, incidents, teams, players, and commit lineage;
- assistant conversations, cases, artifacts, traces, and confirmation receipts;
- webhook and Telegram ingress;
- `/healthz`, `/readyz`, release guards, and current-release drop-in.

## 14. Duplicate and overlapping logic still requiring caution

- canonical and legacy presupuesto routes coexist;
- direct Postgres and Supabase query paths coexist;
- OCR has multiple engines and adapters;
- finance projection logic spans admin routes, services, assistant adapters, and exports;
- document/payment workflows appear in web, Telegram, assistant actions, and reconciliators;
- runtime and roadmap documents contain statuses that became stale after rapid implementation.

Prefer canonical services and read models. Do not add another direct query or mutation path to solve a presentation defect.

## 15. Open questions requiring human or production evidence

- Which exact commit is currently deployed in production?
- Have #303 and #306 been deployed and has the idempotent reimbursement reconciliation run?
- Which references besides Operaciones 96 were affected, and what are their final states?
- When will #304 be updated against `main`, merged, and deployed?
- Which Finance UAT cases have actually been executed and accepted?
- Are issued CFDI, SAT, bank, COI, and SAE integrations live, automated, manual-import, or export-only per workflow?
- Which tournaments have complete SOUL and Owner Pack source coverage?
- Which marketing/sponsor functions persist state and deliver externally, versus producing read-only templates?
- Which SOW deliverables have written client acceptance?
- What support/SLA terms are currently agreed, distinct from the 2025 proposed annex?

## 16. Recommended next sequence

1. Update #304 with current `main`, rerun visible gates, resolve branch-protection check configuration, and merge.
2. Deploy the exact resulting commit using the canonical release drop-in.
3. Run the reimbursement reconciliation and produce a reference-by-reference before/after receipt.
4. Execute the Finance UAT document with real users and preserve evidence.
5. Freeze a client-facing scope matrix: accepted, accepted with observations, pending defect, requested change, and out of scope.
6. Close remaining SOW lanes explicitly: FMF, global dedup, scheduling/logistics, live ERP integration, sponsor approval/publication, proof-of-performance, onboarding/handoff, and support.
7. Update roadmap docs whose statuses now understate AR, cashflow, and Sam Inbox.

## 17. Final conclusion

SamChat has crossed the threshold from a collection of modules into a substantial operational system with a coherent finance spine and a governed assistant architecture. The constraint is no longer raw feature absence. It is controlled convergence: one runtime truth, canonical workflow ownership, production reconciliation, UAT evidence, and contractual closure.
