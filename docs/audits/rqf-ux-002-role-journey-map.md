# RQF-UX-002 — Role & Journey Map

Status: INITIAL_REPOSITORY_SWEEP
Date: 2026-09-21
Issue: #352
Repository baseline refreshed through `main` at `49155be05f311a1c397a9ae179acee688d029f8a`
Full-width operational layout from #351, browser harness #356, effective-profile simulation #359, Direction responsive fix #360, Direction discoverability #362 and Payment Run role-language fix #370 are included in the refreshed repository baseline.

## Purpose

Map SamChat by the work each user is trying to complete. This is a repository-derived baseline, not authenticated UAT and not a claim of business acceptance.

Evidence classes used below:

- `REPO_CONFIRMED`: route, contract, service or test exists in current `main`.
- `DOC_UAT_PENDING`: a repository UAT document defines the flow but records it as pending.
- `HYPOTHESIS`: usability interpretation to validate with users.
- `NOT_MEASURED`: clicks, time, backtracks and error rate have not been measured in browser UAT.

Primary evidence:
- `docs/sprints/rqf-fin-uat-001-finance-integral-uat.md`
- `docs/sprints/rqf-ui-001-table-action-usability.md`
- `docs/audits/rqf-ui-001-table-surface-inventory.md`
- `src/devnous/gastos/services/access_control_service.py`
- `src/devnous/gastos/routes/support_routes.py`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`

## User groups observed in the live-web repository

| User group | Repository evidence | Primary work |
| --- | --- | --- |
| Empleado / solicitante | `ALL_ROLES`; Gastos tools | Crear solicitudes, informes, gastos, adjuntar evidencia y consultar estado |
| Coordinador | Access-control role plus document visibility | Consulta, coordinación y acceso ampliado a documentos |
| Aprobador | Approval routes and `/documentos/pendientes` | Revisar contexto y aprobar/rechazar |
| Control Presupuestal | Budget-control route and named/operator gates | Asignar concepto/partida y destrabar documentos |
| Finanzas / Tesorería | Finance-admin tools, Payment Run, payment queues | Programar, ejecutar y evidenciar pagos |
| Contabilidad | Accounting routes, COI, CxP/CxC, conciliación | Clasificar, conciliar, exportar y cerrar |
| Administración de Presupuestos | Canonical budget routes | Presupuesto, partidas, plan mensual, actuals e ingresos |
| Dirección | Position-scoped executive routes | Lectura ejecutiva y drill-down dentro de portfolio autorizado |
| Soporte | `/soporte` and `/admin/soporte` | Reportar, triagear y resolver incidencias |
| Superadmin / configuración | Access-control and admin configuration surfaces | Control de acceso, catálogos y configuración avanzada |

Important: access is not purely role-based. Current code includes area rules, named catalog-admin access and position-scoped Direction authorization. UI usability must not assume that two users with the same nominal role see identical navigation.

---

# Critical journeys

## J01 — Crear solicitud de transferencia a tercero/proveedor

**Role:** empleado / solicitante  
**Goal:** pedir una transferencia y dejarla lista para autorización.  
**Entry surfaces:** `/gastos-terceros`, `/documentos/nueva-solicitud-terceros`; related anticipo form `/gastos-terceros/solicitar-anticipo`.  
**Downstream:** document detail → approval queue → payment flow.  
**Evidence:** `REPO_CONFIRMED`; finance end-to-end acceptance remains `DOC_UAT_PENDING`.

Current conceptual path:

`Solicitudes de transferencia → crear → seleccionar beneficiario/cuenta/proyecto → enviar → esperar aprobación`

Usability questions:
- Is the distinction between “solicitud a terceros”, “anticipo” and “solicitud personal” evident before entering a form?
- Can the user tell which fields are mandatory because of the business case rather than the form implementation?
- After submit, does the user immediately understand who has it and what happens next?

Metrics: `NOT_MEASURED`.

## J02 — Crear anticipo personal / beneficiario registrado

**Role:** empleado / solicitante  
**Goal:** solicitar fondos antes de incurrir el gasto.  
**Entry surfaces:** `/documentos/nueva-solicitud-personal`, `/gastos-terceros/solicitar-anticipo` depending on case.  
**Evidence:** `REPO_CONFIRMED`; finance UAT case 1.2 is `PENDING_FINANCE_UAT`.

Potential cognitive load: multiple creation entry points represent business variants but are exposed through different route families.

## J03 — Crear informe de gastos

**Role:** empleado / solicitante  
**Goal:** abrir un informe para comprobar gastos y, when applicable, tie it to beneficiary/project/advance.  
**Entry:** `/informes-de-gastos` → `/informes-de-gastos/crear`.  
**Evidence:** `REPO_CONFIRMED`.

Key downstream routes include:
- detail: `/informes-de-gastos/{cuenta_id}`
- edit: `/informes-de-gastos/{cuenta_id}/editar`
- quick expense: `/informes-de-gastos/{cuenta_id}/gastos/quick`
- AMEX marking: `/informes-de-gastos/{cuenta_id}/gastos/amex`
- close/send: `/informes-de-gastos/{cuenta_id}/cerrar`
- settlement: `/informes-de-gastos/{cuenta_id}/saldar`

Metrics: `NOT_MEASURED`.

## J04 — Capturar comprobantes / CFDI dentro de un informe

**Role:** empleado / solicitante  
**Goal:** add expense evidence accurately without needing accounting expertise.  
**Entry:** report detail → quick expense / expense detail.  
**Evidence:** finance UAT defines XML/PDF, tips and budget-line checks; current acceptance is pending.

Usability questions:
- Is the system asking the user only for information they actually know?
- Are accounting-only concepts deferred to Finance/Control where possible?
- Does a failed CFDI validation say what to fix and whether the report can continue?

## J05 — Corregir un documento rechazado y reenviarlo

**Role:** empleado / solicitante  
**Goal:** understand the rejection, repair only what is wrong and resume the same workflow.  
**Entry:** user workspace / document detail.  
**Evidence:** rejection states and edit/cancel behavior are tested in the repository.

Usability risk hypothesis: a status label alone is insufficient; the detail needs “qué se rechazó / quién / por qué / qué debes hacer / reenviar”.

## J06 — Entender el estado de una solicitud o informe

**Role:** any document owner  
**Goal:** answer “¿dónde está y qué sigue?” without knowing `Documento.estado`.  
**Entry:** `/gastos-terceros`, `/informes-de-gastos`, `/documentos/todos`, document detail.  
**Evidence:** current code exposes human status helpers and several list/detail surfaces.

Structural risk: the same business object is discoverable through multiple lists, which can improve access but can also require users to understand the differences among Solicitudes, Documentos and Informes.

## J07 — Aprobar o rechazar una solicitud

**Role:** aprobador  
**Goal:** find pending items, understand enough context and decide safely.  
**Entry:** `/documentos/pendientes` → `/documentos/{documento_id}`.  
**Evidence:** `REPO_CONFIRMED`; RQF-UI-001 records prior button overlap and long-table navigation issues.

Required user answer:
1. What am I approving?
2. Who requested it and for whom?
3. Amount/project/evidence?
4. Why is it in my queue?
5. What happens if I approve or reject?

Metrics: `NOT_MEASURED`.

## J08 — Consultar historial de aprobaciones

**Role:** aprobador / Finanzas / auditor  
**Goal:** reconstruct who decided what and when.  
**Entry:** `/documentos/historial-aprobador`; document detail.  
**Evidence:** `REPO_CONFIRMED`.

UX question: is history part of the object context, or does the user have to leave the object and search another module?

## J09 — Asignar presupuesto / Control Presupuestal

**Role:** Control Presupuestal  
**Goal:** identify the correct project/phase/concept and advance the document.  
**Entry:** `/documentos/control-presupuestal`.  
**Evidence:** `REPO_CONFIRMED`; budget-control regression tests exist.

Usability questions:
- Does the queue explain why an item is blocked?
- Does the selector expose only valid choices for the current context?
- After save, is advancement explicit rather than inferred from disappearance from the queue?

This is a high-value UAT journey because wrong classification is both a usability and financial-control risk.

## J10 — Revisar aprobados pendientes de pago

**Role:** Finanzas / Tesorería  
**Goal:** distinguish approved-but-unpaid work from scheduled/in-process/paid work.  
**Entry:** `/documentos/pendientes-pago`, `/admin/finanzas/payment-run`.  
**Evidence:** canonical Payment Run states documented in engineering canon.

Critical semantic boundary:
- `aprobado` unpaid = eligible
- `en_proceso_pago` = in closed cutoff, not proof of payment
- `pagado` / payment evidence = paid

UX must make this boundary visible in human language.

## J11 — Ejecutar Payment Run

**Role:** authorized Finance/Tesorería operator  
**Goal:** schedule/close an operational payment cutoff without falsely implying money moved.  
**Entry:** `/admin/finanzas/payment-run`.  
**Related:** closure detail `/admin/finanzas/payment-run/closures/{closure_id}`.  
**Evidence:** `REPO_CONFIRMED`, business UAT pending.

Key interaction questions:
- Can the operator distinguish selection, scheduling, cutoff closure and actual payment?
- Are counts and totals visible before irreversible actions?
- Is a confirmation receipt available after closure?

## J12 — Cargar testigo / confirmar pago

**Role:** Finanzas / Contabilidad authorized user  
**Goal:** attach proof and transition to paid.  
**Entry:** Payment Run in-process section and/or document payment surface.  
**Evidence:** `REPO_CONFIRMED`; finance UAT pending.

Risk: the interface must not visually collapse “closed cutoff” and “paid”.

## J13 — Limpiar clasificación contable / preparar COI

**Role:** Contabilidad / Finanzas  
**Goal:** identify exactly what is missing before COI export, fix it and verify readiness.  
**Entry:** `/admin/gastos/sin-cuenta-contable`.  
**Follow-up:** `/admin/contabilidad/coi`, preview/export routes.  
**Evidence:** `REPO_CONFIRMED`; finance UAT explicitly recommends this as first Finance walkthrough.

Usability questions:
- Does each row state the blocker, not merely show blank fields?
- Can the user see origin document and navigate back?
- Does fixing a blocker visibly remove only the resolved condition?

## J14 — Revisar / exportar pólizas COI

**Role:** Contabilidad  
**Goal:** verify accounting lines and produce review/load artifact.  
**Entry:** `/admin/contabilidad/coi`; detail `/admin/contabilidad/coi/{poliza_id}`.  
**Related exports:** batch XLSX/ZIP and per-document/per-report previews.  
**Evidence:** `REPO_CONFIRMED`; finance UAT pending.

Potential friction: multiple entry points and export variants can be useful for power users but need task-oriented grouping.

## J15 — Conciliar banco contra SamChat

**Role:** Contabilidad / Tesorería  
**Goal:** review candidate matches, accept/reject with evidence, audit decisions.  
**Entry:** `/admin/contabilidad/conciliacion`.  
**Detail:** `/admin/contabilidad/conciliacion/{movement_id}`.  
**Audit:** `/admin/contabilidad/conciliacion/auditoria`.  
**Evidence:** `REPO_CONFIRMED`; business UAT pending.

Important rule: candidate bank match is not payment/collection proof. UI language must preserve that distinction.

## J16 — Operar Cuentas por Cobrar

**Role:** Finanzas / Contabilidad  
**Goal:** connect expected income, issued CFDI, project/budget classification, collection and accounting.  
**Repository routes/docs:** both `/admin/finanzas/cuentas-por-cobrar` and `/admin/contabilidad/cuentas-por-cobrar` are registered in current repository evidence.  
**Evidence status:** `REPO_CONFIRMED_DUAL_SURFACE`.

The Finance route consumes the canonical AR read model and exposes portfolio, billing schedule, actionable gaps, matching and CxC exports. The Accounting route is a separate contabilidad-context surface and is linked from the Finance AR UI as “Vista contable”. The UX question is therefore not which route exists, but whether users understand the purpose of each and can move between them without treating them as duplicate competing homes.

## J17 — Administrar Presupuestos

**Role:** authorized budget/Finance admin  
**Goal:** inspect budget by tournament, manage lines/monthly plan and connect income.  
**Canonical owner:** `src/devnous/gastos/routes/admin_budget_routes.py`.  
**Entry:** `/admin/presupuestos` → `/admin/presupuestos/torneo/{tournament_key}`.  
**Evidence:** route policy explicitly distinguishes canonical owner, bridges and legacy candidates.

Usability risk: legacy/bridge routes create implementation complexity; navigation must surface only canonical user-facing concepts.

## J18 — Dirección: identificar atención y drill down

**Role:** eligible internal Direction position  
**Goal:** understand portfolio/tournament state and drill into evidence without write authority.  
**Entry:** `/direccion/tableros`, `/direccion/reportes`.  
**Evidence:** engineering canon defines position-scoped, read-only consumption.

Usability requirement: executive views should lead with exceptions/attention, not expose the full operational taxonomy by default.

## J19 — Crear y seguir ticket de soporte

**Role:** any active user  
**Goal:** report a problem and see its status.  
**Entry:** `/soporte` → `/soporte/nuevo` → `/soporte/{id}`.  
**Evidence:** support route module explicitly documents this flow.

## J20 — Triage y resolver soporte

**Role:** superadmin/support staff  
**Goal:** review all tickets, assign priority/status/owner, respond and close.  
**Entry:** `/admin/soporte`; system view `/admin/soporte/estado-sistema`.  
**Evidence:** `REPO_CONFIRMED`.

---

# Cross-journey observations from the repository

## O1 — More than one navigation hierarchy exists

Repository evidence contains:
- global top navigation;
- Gastos workspace navigation;
- accounting subnavigation;
- admin navigation;
- breadcrumbs;
- support admin tabs;
- route manifests/access-control tool groups.

This is not automatically a defect. It is a cognitive-load hypothesis that must be tested by task completion.

## O2 — Same object, multiple queue contexts

A `Documento` can appear in owner lists, all-documents views, approval queues, budget-control queues and payment queues depending on state/authority.

The product should explain **why this object is here now** rather than relying on the user to infer state-machine semantics.

## O3 — Finance work is distributed across several hubs

Confirmed route families include:
- `/admin/gastos`
- `/admin/finanzas`
- `/admin/contabilidad`
- `/admin/gastos/sin-cuenta-contable`
- `/admin/presupuestos`
- document/report surfaces under non-admin paths.

This distribution is a major information-architecture question for UAT.

## O4 — Business acceptance is still missing for several finance journeys

The integral Finance UAT document remains `PENDING_FINANCE_UAT` for the major flows. Repository code is not enough to claim usability or end-to-end acceptance.

# Next evidence needed

For J01–J20, authenticated browser UAT must record:
- starting page;
- exact task prompt;
- route sequence;
- time;
- clicks/taps;
- backtracks;
- error/recovery;
- help requests;
- final state;
- screenshot/recording reference.

Until that evidence exists, statements about “easy”, “confusing”, “fast” or “intuitive” remain hypotheses.
