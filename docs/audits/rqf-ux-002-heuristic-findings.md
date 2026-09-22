# RQF-UX-002 — Initial Heuristic Findings

Status: INITIAL_REPOSITORY_SWEEP
Date: 2026-09-21
Baseline refreshed through `main@49155be0`
Issue: #352

This is a repository-based usability audit. It separates confirmed structure from interpretation. Priority is provisional until authenticated task UAT.

Evidence levels:
- `CONFIRMED_REPO`
- `OBSERVED_IN_REPO_UAT_DOC`
- `HYPOTHESIS_TO_VALIDATE`
- `DUAL_SURFACE_REQUIRES_UAT`

## Top 20 findings

| ID | Candidate priority | Finding | Evidence | Why it matters | Required validation |
| --- | --- | --- | --- | --- | --- |
| UX-001 | P1 | Finance work is split across several plausible hubs | CONFIRMED_REPO | Users may need to know whether a task belongs to Gastos, Finanzas, Contabilidad, Presupuestos or Limpieza | First-click tests by Finance task |
| UX-002 | P1 | The product has multiple navigation layers | CONFIRMED_REPO | Global nav + workspace nav + accounting subnav + admin nav + breadcrumbs can create orientation cost | Observe task starts and backtracks |
| UX-003 | P1 | Browser journey coverage was missing at the initial sweep | RESOLVED_FOUNDATION | Playwright harness #356 and effective-profile baseline #359 are merged; completion coverage is still expanding | Continue critical-journey coverage under #363/#365 |
| UX-004 | P1 | Major Finance journeys remain `PENDING_FINANCE_UAT` | CONFIRMED_REPO | Repo-live does not prove the workflow is usable or accepted | Run task UAT with Finance |
| UX-005 | P1 | Same Documento moves through several queues and lists | CONFIRMED_REPO | Users may have to infer state-machine meaning from where the item appears | Test “where is this and what happens next?” |
| UX-006 | P1 | Payment Run has a critical semantic distinction between cutoff and paid | CONFIRMED_REPO | UI ambiguity can cause operational errors | UAT cutoff vs payment-proof tasks |
| UX-007 | P1 | Control Presupuestal is a high-risk classification queue | CONFIRMED_REPO | Wrong selection is costly; disappearance from queue is not sufficient feedback | Error-prevention and completion UAT |
| UX-008 | P1 | Solicitud creation is split across `/gastos-terceros` and `/documentos` route families | CONFIRMED_REPO | Taxonomy may be clear to developers but not requesters | First-click test for 3 request types |
| UX-009 | P1 | CxC has separate Finance and Accounting surfaces with the same business label | DUAL_SURFACE_REQUIRES_UAT | Users may treat purpose-specific views as duplicates or choose the wrong one | Test task-to-view mapping and cross-navigation |
| UX-010 | P1 | Prior table/action usability defects were user-observed | OBSERVED_IN_REPO_UAT_DOC | Confirms that passing functional tests did not guarantee usable UI | Continue browser UAT after #351 |
| UX-011 | P2 | Legacy/bridge/canonical budget routes coexist | CONFIRMED_REPO | Implementation history can leak into user navigation if not controlled | Reachability/link inventory |
| UX-012 | P2 | Specialized grids and legacy inline tables remain outside shared UI contract | CONFIRMED_REPO | Inconsistent behavior is likely at viewport/keyboard boundaries | Dedicated browser UAT |
| UX-013 | P2 | Status is a business-state primitive, but human “next step” is not proven consistently | CONFIRMED_REPO + HYPOTHESIS | Users care about action, not internal enum | Detail-page audit by state |
| UX-014 | P2 | Terminology mixes Spanish business language with English/internal labels such as “Payment Run” | CONFIRMED_REPO | Mixed vocabulary can slow non-technical users | Terminology comprehension test |
| UX-015 | P2 | Search/filter features appear to have been added route-by-route | CONFIRMED_REPO | Inconsistent filters can increase relearning and lost context | Compare list views and filter persistence |
| UX-016 | P2 | Access visibility is not purely role-based | CONFIRMED_REPO | Users with similar titles may see different menus; support/training can become difficult | Render nav for representative effective profiles |
| UX-017 | P2 | Approval history and workflow work queues are separate destinations | CONFIRMED_REPO | Audit context may require leaving the object being reviewed | Test “why was this approved/rejected?” |
| UX-018 | P2 | COI work has cleanup, preview, detail and multiple export entry points | CONFIRMED_REPO | Power is useful, but task grouping may be unclear | UAT with accounting operators |
| UX-019 | P2 | Direction is read-only and scope-constrained but consumes cross-domain data | CONFIRMED_REPO | Executive UX should simplify without implying write authority | Direction attention/drill-down UAT |
| UX-020 | P2 | Support system has its own route visibility manifest in addition to access-control tooling | CONFIRMED_REPO | Duplicate inventories can drift and confuse diagnostics/support | Compare route manifest vs effective-access catalog |

---

# Detailed findings

## UX-001 — Fragmented Finance entry architecture

Current repository surfaces include:
- `/admin/gastos`
- `/admin/finanzas`
- `/admin/contabilidad`
- `/admin/gastos/sin-cuenta-contable`
- `/admin/presupuestos`
- `/documentos/pendientes-pago`

This is not inherently wrong: domains have different authorities. The usability risk is forcing a user to decide the owning domain before they can state the job.

**Target question:** can a Finance user start with “what needs my attention?” while preserving domain ownership underneath?

## UX-002 — Navigation depth and mental model

The repository contains distinct global, workspace, accounting, admin and local-tab navigation components. Breadcrumbs improve local context but do not eliminate the need to choose correctly at entry.

**Do not fix by flattening permissions.** The audit must simplify discovery without bypassing authority.

## UX-003 — Missing browser-level contract

Search of current repository did not return Playwright, Selenium or axe usage. Existing UI contracts inspect source/CSS and are valuable but cannot verify:
- real sticky behavior;
- tab order;
- focus visibility;
- actual horizontal/vertical scroll interaction;
- post/redirect continuity;
- preservation of filters on back navigation;
- live error messages;
- route-to-route task completion.

## UX-004 — UAT gap

`docs/sprints/rqf-fin-uat-001-finance-integral-uat.md` defines a strong end-to-end finance protocol but remains pending for major flows.

This audit should reuse those cases instead of inventing a parallel finance acceptance plan.

## UX-005 — Queue semantics over object semantics

The same document can appear in:
- user-owned lists;
- all documents;
- approvals;
- Control Presupuestal;
- payment queues;
- Payment Run/history.

A user should not need to know the entire state machine to understand why the object is in a specific queue.

Recommended detail contract:
- current state in human language;
- why it is here;
- who owns the next action;
- what evidence is missing;
- what happens next.

## UX-006 — Payment cutoff versus actual payment

Engineering canon explicitly states that closing Payment Run does not prove money moved.

Usability implication: never rely only on colors such as “success” for `en_proceso_pago`. Wording and evidence must distinguish:
- listo para corte;
- dentro de corte;
- comprobante pendiente;
- pagado confirmado.

## UX-007 — Control Presupuestal error prevention

This journey combines financial classification and workflow transition. It should be treated as a high-risk decision UI.

Audit:
- option scoping;
- explanation of disabled/unavailable concepts;
- confirmation of selected phase/concept;
- explicit success receipt;
- recovery after wrong selection;
- duplicate submission prevention.

## UX-008 — Request type discovery

The repository has different creation paths for tercero/proveedor, personal and anticipo. The UX should first ask “¿qué necesitas hacer?” rather than require route knowledge.

No route consolidation is proposed here; this is an IA hypothesis.

## UX-009 — Dual CxC surfaces

Repository evidence confirms:
- `/admin/finanzas/cuentas-por-cobrar` with `build_ar_read_model`, billing schedule, actionable gaps, matching, detail and export routes;
- `/admin/contabilidad/cuentas-por-cobrar` with accounting navigation/breadcrumb context.

The Finance AR UI explicitly links to the second as “Vista contable”.

The issue is therefore purpose communication, not missing route ownership. UAT must determine whether Finance and Contabilidad users understand which view answers which task.

## UX-010 — Prior real usability defect class

RQF-UI-001 records:
- action labels breaking;
- opposing actions too close;
- losing header context in long tables;
- horizontal navigation only at end.

PR #351 is merged and addresses layout/scroll behavior at repository level; browser and deployed-runtime evidence are still necessary before claiming usability acceptance.

## UX-011 — Presupuestos route debt

The route policy documents canonical, bridge, legacy and candidate-hide/remove concepts.

Product requirement: normal users should see one Presupuestos mental model. Compatibility implementation should remain invisible.

## UX-012 — Specialized/legacy visual debt

RQF-UI-001 inventory names:
- specialized budget grids;
- legacy teams/players;
- legacy support inline tables;
- reporting compatibility routes.

These require explicit disposition; “shared CSS fixed everything” is not a valid acceptance claim.

## UX-013 — Human next-step consistency

The repository has human status-label work, but no single evidence artifact proves that every critical detail view answers “qué sigue”.

Audit each document state with screenshots and one question:
> Si te dejo aquí sin capacitación, ¿sabes qué debe pasar después?

## UX-014 — Terminology

Candidate glossary pairs to test:
- Payment Run ↔ Programación de pagos / Corte de pagos
- Documento ↔ Solicitud / Informe
- Limpieza contable ↔ Pendientes de clasificación contable
- Testigo de pago ↔ Comprobante de pago
- Control Presupuestal ↔ Asignación presupuestal

Do not rename accounting/business terms without operator validation.

## UX-015 — Filter inconsistency

Repository history shows route-specific filter additions such as provider/action/requester filters and COI month fixes. This indicates filter behavior has evolved locally.

Audit standard:
- same position;
- same “clear filters” affordance;
- active-filter count;
- preserved query on detail/back;
- dates use consistent business semantics;
- no default bulk selection unless explicitly intended.

## UX-016 — Effective-access variability

Because access rules can be role-, area-, named-user- and position-scoped, a single screenshot of “the menu” is not representative.

UAT needs fixtures for effective profiles, not only nominal roles.

## UX-017 — History context

If approval history requires switching to a separate global history page, operators may lose context. Verify whether object detail already provides sufficient evidence; if it does, the global history page can remain a search/audit tool.

## UX-018 — COI path complexity

Accounting users have cleanup, COI list, policy detail, line correction, preview and batch/per-object exports.

This may be correct expert workflow. Validate sequence and preferred starting point before simplifying.

## UX-019 — Direction

Direction must be easy to scan while preserving:
- read-only status;
- active portfolio/tournament scope;
- explicit missing-data gaps;
- no manufactured zeros.

The target UX should favor attention items and drill-down.

## UX-020 — Duplicate route catalogs

`support_routes.ROUTE_MANIFEST` and `access_control_service.ACCESS_TOOLS` serve different purposes, but both describe route visibility/categories.

Audit whether diagnostics can report a route as “expected” when effective access/UI navigation says otherwise.

---

# Non-findings / things not yet proven

The initial repository sweep does **not** prove:
- that users cannot complete tasks;
- that a sidebar is required;
- that fewer screens is always better;
- that every Finance user should see the same home;
- that all English terminology is wrong;
- that route consolidation is safe;
- that #351 is deployed or accepted.

Those require browser/runtime evidence.
