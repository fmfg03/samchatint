# RQF-UX-002 — Candidate Implementation Roadmap

Status: PROPOSAL_AFTER_AUDIT — NO PRODUCT CHANGES AUTHORIZED
Date: 2026-09-21
Issue: #352
Baseline: `main@c53a6591`

## Rule

This roadmap is sequencing, not authorization. No implementation PR should start solely because it appears here. Each item must be supported by UAT evidence and preserve canonical routes, authority and business state.

## Phase A — Evidence foundations

### UX-PR-A1 — Browser harness
Scope:
- Playwright harness;
- authenticated test fixture;
- screenshots/traces;
- one read-only smoke.

No product UI change.

### UX-PR-A2 — Navigation inventory contract
Scope:
- machine-readable inventory of user-facing route labels/targets where practical;
- tests for canonical vs legacy visible entry points;
- no navigation redesign.

Goal: prevent accidental duplicate visible destinations.

### UX-PR-A3 — Effective-profile snapshots
Scope:
- test fixtures for representative effective access:
  - employee;
  - approver;
  - budget control;
  - finance/payment;
  - accounting;
  - Direction;
- render/capture expected navigation without weakening access policy.

## Phase B — Shared interaction contracts

Only after UAT confirms cross-surface patterns.

### UX-PR-B1 — “Qué pasa ahora” detail contract
Target journeys: J05, J06, J07, J10, J12.

Add a shared presentation pattern showing:
- human status;
- why the item is here;
- next owner;
- next expected action;
- blocker/evidence gap.

No state transition changes.

### UX-PR-B2 — Return/context preservation
Target journeys: J06–J15.

Standardize:
- `next`/return URL safety;
- preservation of search/filter state;
- consistent “volver” behavior.

### UX-PR-B3 — List/filter interaction contract
Target list/queue surfaces.

Standardize:
- filter placement;
- active filter count;
- clear filters;
- query persistence;
- empty result copy.

Do not change business query semantics in the same PR.

### UX-PR-B4 — Success/error receipt contract
Target mutation forms.

Standardize:
- what changed;
- resulting state;
- next owner/action;
- safe retry/idempotency guidance.

## Phase C — Employee / approver journeys

### UX-PR-C1 — Request creation chooser
Targets J01/J02.

A task-level chooser routes to existing canonical forms:
- transferencia tercero/proveedor;
- anticipo;
- solicitud personal where applicable.

No route removal.

### UX-PR-C2 — “Mis asuntos” employee entry
Targets J03–J06.

Read-only aggregation/deep links:
- drafts;
- rejected;
- open reports;
- waiting on others;
- recently completed.

No duplicate source of truth.

### UX-PR-C3 — Approval queue decision context
Target J07/J08.

Improve queue/detail information sufficiency and explicit decision receipt. Preserve exact POST routes and authorization.

## Phase D — Financial control journeys

### UX-PR-D1 — Control Presupuestal clarity
Target J09.

Improve:
- blocker explanation;
- phase/concept context;
- valid-option visibility;
- explicit post-assignment receipt.

No inferred financial selection.

### UX-PR-D2 — Payment lifecycle wording
Targets J10–J12.

Make distinctions explicit:
- aprobado/listo para corte;
- en corte / en proceso;
- comprobante pendiente;
- pagado.

No state model change.

### UX-PR-D3 — Payment Run task grouping
Target J11/J12.

Reduce visual ambiguity among:
- selection;
- cutoff closure;
- proof upload;
- recent closures.

Preserve authority and state transitions.

### UX-PR-D4 — Accounting cleanup blocker-first UI
Target J13.

Rows lead with exact blocker/reason and origin context before editing controls.

### UX-PR-D5 — COI task grouping
Target J14.

Organize:
- pendientes;
- preview/review;
- exports;
- history/detail.

Do not merge accounting artifacts or remove expert exports.

### UX-PR-D6 — Conciliation evidence hierarchy
Target J15.

Visually distinguish:
- candidate match;
- accepted match;
- evidence;
- audit/reversal.

No automatic acceptance.

### UX-PR-D7 — CxC dual-surface clarity
Target J16.

If UAT confirms both current surfaces are needed:
- Finance route label/subtitle emphasizes portfolio/facturación/cobranza workbench;
- Accounting route label/subtitle emphasizes “Vista contable”;
- reciprocal cross-links preserve filters/context.

No route renaming in first cut.

## Phase E — Navigation / home

Only after measured first-click evidence.

### UX-PR-E1 — “Mi trabajo” read-only landing
Aggregate existing authorized queues and deep links.

Must not:
- create new workflow state;
- copy/own financial data;
- manufacture counts from noncanonical sources;
- expose inaccessible items.

### UX-PR-E2 — Domain navigation simplification
Use UAT evidence to reduce duplicate peer labels and place administrative/configuration destinations behind appropriate grouping.

### UX-PR-E3 — Search entry
Cross-domain authorized search into canonical detail pages.

Separate project if search semantics require new indexing/query work.

## Phase F — Direction

### UX-PR-F1 — Attention-first executive landing
Target J18.

Prioritize:
- exceptions;
- aging;
- missing evidence;
- drill-down.

Preserve position/portfolio scope and read-only contract.

## Phase G — Legacy retirement

Only after reachability evidence.

### UX-PR-G1+ — One legacy family per PR
Candidates from existing docs:
- Presupuestos legacy routes;
- legacy Teams/Players templates;
- reporting compatibility routes;
- legacy support inline surfaces.

Each retirement requires:
- inbound-link inventory;
- route/form dependency tests;
- redirect compatibility where needed;
- rollback.

## Suggested order

1. A1 browser harness.
2. A2/A3 evidence foundations.
3. Run UAT baseline.
4. B1/B2/B4 detail/context contracts.
5. C1–C3 employee/approval.
6. D1–D7 finance/accounting.
7. Rerun UAT and compare metrics.
8. E1/E2 only if navigation evidence supports them.
9. F1 Direction.
10. G-series legacy retirement.

## PR guardrails

Every UX implementation PR should state:
- journey ids;
- before evidence;
- expected measurable improvement;
- routes touched;
- authority/state invariants preserved;
- browser test added/updated;
- rollback;
- UAT status.

Avoid “global UX cleanup” PRs that mix unrelated journeys.
