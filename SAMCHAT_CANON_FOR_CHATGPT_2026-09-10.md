# SamChat Canon for ChatGPT

Version: 2026-09-10

Repository evidence baseline: `fmfg03/samchatint` `main` at `7684713cc2e529b8f48768553cd1acc6f7e1e39b`

Contract baseline: “Oferta Plataforma Sports SP,” dated 2025-08-28

## 1. Canonical definition

SamChat is the operational system for Plataforma Sports workflows. Its intended primary interface is a governed business assistant: the user states an objective, SamChat inspects current context and evidence, uses canonical business tools, prepares an auditable result, and executes real effects only through explicit authority.

The dashboard is an important operating and supervision surface. It is not the whole product and it is not evidence that the assistant can complete every workflow end to end.

## 2. The four truths that must not be conflated

| Truth | Meaning |
| --- | --- |
| Contract truth | What the signed proposal/SOW describes or promises |
| Repository truth | What exists and is connected in the inspected `main` commit |
| Runtime truth | What is deployed and observable in `sam.chat` |
| Acceptance truth | What Plataforma Sports users have validated and accepted in real operation |

When these disagree, preserve all four. Never upgrade a contractual promise into a live capability, or a repository capability into an accepted deliverable.

## 3. Current product spine

### Core operations

- empleados, sessions, roles, permissions, admin and user surfaces;
- solicitudes, informes, cuentas de gastos, anticipos, reembolsos, devoluciones, approvals, and payments;
- providers/beneficiaries, banking instructions, notifications, support, and audit;
- registration review, OCR, teams, players, incidents, and commit controls.

### Finance spine

- Finance command center;
- Payment Run and payment history;
- payment-order XLSX by cutoff;
- CFDI intake, linking, tax readiness, COI and DIOT-related surfaces;
- budgets, versions, concepts, lines, monthly/weekly allocations, actuals, and movement reconciliation;
- accounts receivable, CFDI-income approval, collection matching, reversals, and COI-ready export;
- cashflow planning read model and UI;
- payroll and AMEX accounting/reconciliation components.

### Assistant spine

- persistent conversations and analyst cases;
- document/media intake;
- governed tool registry;
- semantic work-frame and candidate adjudication;
- evidence quality and answer sufficiency checks;
- executive rendering and trace;
- read-only finance adapter;
- Owner Pack/entity folder readiness and evidence views;
- preview/confirmation boundaries for write-capable actions.

### Cross-domain operating views

- Sam Inbox as a role-aware, read-only queue assembled from existing sources;
- runtime artifact index as discoverability, not a universal artifact archive;
- sports/tournament command-center and dossier projections;
- SOUL coverage/draft/preview support for tournament context.

## 4. Product north star

The target interaction is:

> The user describes a business outcome, supplies files when needed, and receives a prepared, evidence-backed result with missing data, assumptions, validation, preview, authority gate, execution receipt, and resumable context.

The product is not complete when a module screen exists. It is complete when the relevant user can finish the real business outcome with evidence and controlled authority.

## 5. Non-negotiable principles

- Live governed sources outrank memory and prose documentation.
- The assistant may investigate and propose; authority stays with people and configured business rules.
- Read and write behavior must be visibly separated.
- Every write requires a canonical action, authority check, explicit confirmation, idempotency, and audit evidence.
- Missing evidence must remain missing; SamChat must not complete a plausible-looking record by invention.
- Financial states must reflect real workflow transitions, not friendly display defaults.
- Operational reference, SamChat reference, source document, approver, beneficiary, payment/collection evidence, and accounting output must remain traceable.

## 6. Current repository status by lane

| Lane | Status in current `main` | What is still not proven |
| --- | --- | --- |
| Gastos and document workflows | Connected and mature | Full Finance UAT and formal customer acceptance |
| Payment Run | Connected, with cutoff, proof, history, and export | End-to-end UAT across all roles and production reconciliation after recent fixes |
| Presupuestos | Connected and substantially expanded | Final reconciliation, catalog cleanup, and user acceptance across all tournaments |
| Accounts receivable | Connected with UI, matching, approvals, accounting, and export | Final issued-CFDI production integration and Finance UAT |
| Cashflow | Connected read model and admin UI | Forecast acceptance and full bank-source completeness |
| Assistant | Connected, governed, and broad read-only capability | A proven general-purpose write cycle equivalent to the product promise |
| Owner Pack/entity folders | Connected read-only views | Complete SOUL/source coverage and durable publication workflow |
| Tournament/OCR | Connected operational review flow | Productive FMF integration and complete contractual dedup/calendar/logistics closure |
| Sports command views | Connected projections | Full transaction-capable tournament operating system |
| Sponsor/marketing automation | Code/projection components exist | Persistent approval, external publishing, engagement analytics, and accepted proof-of-performance cycle |

## 7. SOW traceability

The 2025 proposal is scope evidence. Its descriptions and KPI percentages are not measurements of delivered performance.

| SOW area | Current evidence | Canonical status |
| --- | --- | --- |
| Project management engine | Tournament operational commitments, SOUL, Sam Inbox, assistant cases, and projections exist | Partial; no single proven general project-management engine |
| Registration, OCR, roster, compliance | Governed registration-review and OCR pipelines, teams/players, incident controls, commit rules | Strong partial / operational |
| FMF validation | Rules and references exist | Productive external integration not proven |
| Global deduplication | Duplicate and integrity controls exist in registration/OCR flows | Partial; contractual global-padrón closure not proven |
| Intelligent scheduling | Sports projections and planning artifacts exist | Not proven as a live optimizer with real venue/referee constraints |
| Uniforms, venues, referees, logistics | Data/projection concepts exist | End-to-end operating workflows not proven |
| Gastos and expense accounts | Extensive live-web models, routes, services, and tests | Strong repository evidence; UAT still required |
| Accounts payable | Solicitudes, approvals, Payment Run, payment proof, accounting/export surfaces | Strong repository evidence; UAT still required |
| Accounts receivable | AR read model, UI, CFDI link approval, collection matching, reversal, accounting, export | Implemented in repository after the June sweep; production/UAT closure unproven |
| Payroll | Substantial models, services, routes, and accounting mapping | Above-baseline implementation; UAT and contractual acceptance still separate |
| Budgets by project | Canonical budget routes and extensive service layer | Strong repository evidence; data cleanup/UAT remain |
| COI/SAE integration | COI exports/import-oriented components exist | Live bidirectional SAE/COI integration not proven |
| Dashboards/reports | Finance, budgets, AR, cashflow, sports, inbox, artifacts, executive exports | Strong repository evidence; do not infer every SOW dashboard is accepted |
| Marketing communications | Existing communications/media surfaces plus sponsor-media projections | Partial |
| Sponsor/branding approval | In-memory workflow/proof-package logic and tests | Not proven as persistent end-to-end production flow |
| Social publishing | No sufficient current evidence of accepted external publication automation | Open |
| Engagement analytics | No sufficient current evidence of complete multichannel analytics | Open |
| Proof-of-performance | Builders/projections exist | End-to-end accepted sponsor delivery not proven |
| Onboarding, training, go-live, support | Documentation and support surfaces exist | Formal contractual closeout package not proven |

## 8. Contract facts that remain contract facts

The attached SOW states:

- 854 estimated hours;
- estimated labor total of MXN 607,200 plus applicable VAT;
- MXN 35,000 plus VAT discount for AI Envision sessions;
- 30% / 40% / 30% payment structure;
- approximately 12 weeks;
- two full training days;
- six months of post-delivery monthly support;
- third-party licenses, hardware, SaaS, PAC, WhatsApp, and cloud costs outside the base amount;
- a suggested maintenance annex of MXN 45,000 plus VAT per month and 20 technical hours.

These numbers must not be silently replaced by later commercial arrangements. Actual invoices, payments, discounts, support caps, and change orders require their own evidence.

The SOW also contains aggressive outcome targets such as 70% faster setup, 90% fewer scheduling errors, 40% lower operating costs, and 90% adoption. Treat them as proposed targets unless a baseline, measurement method, observation window, and accepted result exist.

## 9. Current critical workflow rule: reimbursement after expense report

For a reimbursement created from an expense report:

1. the `INFORME` is the budget-controlled artifact;
2. the `INFORME` must be classified and approved;
3. the reimbursement request inherits the budget concept and approver;
4. the reimbursement request does not return to Control Presupuestal;
5. it enters Payment Run only after those conditions are satisfied;
6. Payment History must not label an ineligible request as `programada`.

Repository state on 2026-09-10:

- #303 merged: recover eligible reimbursements stuck in Control Presupuestal;
- #306 merged: exclude ineligible requests from false scheduled-payment history;
- #304 merged and deployed as `dcbeea8423030e0123101a35378d765ea3c96f32`:
  require a budget concept on the approved linked `INFORME` before promotion.

The deployed guard does not by itself close the business incident: historical
reconciliation and affected-reference verification remain required, and Finance
UAT is separate evidence.

## 10. Current closure gates

A finance capability is not “done” until it passes a real UAT path showing:

- requester and beneficiary;
- authorization route and approver;
- operational and SamChat references;
- source document and evidence;
- payment or collection proof;
- generated accounting entry;
- downloadable COI-ready output where applicable;
- final state visible in the correct operational board;
- no contradictory state in adjacent boards.

The repository UAT document remains `PENDING_FINANCE_UAT` across its listed cases at the inspected baseline.

## 11. Product priorities

1. Preserve and verify the deployed #304 reimbursement guard while completing
   historical reconciliation and reference-by-reference evidence.
2. Run focused production reconciliation for Referencia Operaciones 96 and every equivalent historical record.
3. Execute the integral Finance UAT and convert failures into bounded defects, not uncontrolled scope growth.
4. Keep stabilizing the assistant's read/evidence loop before opening broad write authority.
5. Complete source coverage for Owner Pack/SOUL before promising complete folders.
6. Treat FMF, scheduling, logistics, sponsor approval/publication, and proof-of-performance as explicit contractual closure lanes.
7. Produce a formal delivery closeout: accepted items, observations, open scope, handoff, support terms, and payment status.

## 12. Common reasoning errors

- Calling SamChat only a chatbot or only a dashboard.
- Treating every repo module as part of the live runtime.
- Saying “implemented” without specifying code, deployment, or acceptance.
- Treating a Payment Run cutoff as proof of payment.
- Treating an AR candidate as proof of collection.
- Treating an assistant artifact as the business source of truth.
- Treating sponsor package generation as external delivery.
- Treating SOW KPIs as achieved results.
- Treating all work added after the SOW as included contractual scope.

## 13. Safe short description

SamChat is Plataforma Sports' governed operational system for tournament, gastos, finance, evidence, and assistant workflows. Its current repository contains substantial live-web functionality across gastos, Payment Run, budgets, AR, cashflow, OCR, Sam Inbox, and a read-mostly governed assistant. The remaining work is not a rebuild; it is production verification, Finance UAT, contractual closure of specific operations/marketing integrations, and proof that the assistant can complete controlled end-to-end business outcomes.
