# RQF-UX-002 — Target Information Architecture Hypothesis

Status: PROPOSAL_FOR_UAT — DO NOT IMPLEMENT YET
Date: 2026-09-21
Issue: #352

## Objective

Reduce the amount of SamChat taxonomy a user must learn before completing work, while preserving all existing authority and canonical domain boundaries.

## Design rule

Every operational screen should answer:

1. **Dónde estoy**
2. **Qué requiere atención**
3. **Qué puedo hacer**
4. **Qué pasa después**

The target IA does not collapse financial domains or permissions. It changes discovery and presentation.

---

# Proposed primary navigation

## 1. Inicio / Mi trabajo

Stable for all authenticated internal users.

Content is a read-only aggregation of existing canonical queues, filtered by current authority.

Examples by effective profile:

### Empleado
- Borradores por terminar
- Rechazados por corregir
- Informes abiertos
- Esperando aprobación
- Pagos/comprobaciones recientes

### Aprobador
- Requieren mi decisión
- Recién corregidos / reenviados
- Historial reciente

### Control Presupuestal
- Sin clasificación
- Con excepción de fase/concepto
- Recién asignados

### Finanzas/Tesorería
- Aprobados listos para corte
- En proceso sin comprobante
- Pagos con excepción
- Aging relevante

### Contabilidad
- Clasificación pendiente
- COI por revisar
- Conciliaciones pendientes
- CxC/ingresos con excepción

### Dirección
- Requiere atención
- Portfolios/torneos
- Excepciones financieras/operativas
- Reportes recientes

Important: this is an aggregation/navigation surface, not a new source of truth and not a new write owner.

## 2. Buscar

Cross-domain search for authorized objects:
- reference SamChat;
- reference Operaciones;
- beneficiary/provider;
- project/tournament;
- document id/reference.

Deep links must land in canonical detail pages.

## 3. Domains

Power-user/expert navigation remains:
- Operaciones
- Gastos
- Finanzas
- Contabilidad
- Presupuestos
- Dirección
- Configuración
- Soporte

Only authorized domains are visible.

---

# Gastos target model

Instead of making users choose a route family first:

## “Crear”
A task chooser:
- Solicitud de transferencia
- Anticipo
- Informe de gastos
- Gasto/comprobante inside existing report

Each choice routes to the existing canonical form.

## “Mis asuntos”
- Borradores
- Abiertos
- Rechazados
- Esperando a otros
- Cerrados/recientes

## “Documentos”
Power-user search/list remains available.

---

# Approval target model

One clear work queue:

**Requieren mi decisión**

Each row/detail should show:
- type;
- requester;
- effective beneficiary;
- amount;
- project;
- evidence completeness;
- reason it reached this approver;
- age;
- primary actions.

After decision:
- explicit receipt;
- resulting state;
- next owner/queue;
- link back preserving filters.

---

# Budget Control target model

**Pendientes de asignación presupuestal**

For each item:
- why blocked;
- project/tournament/phase;
- existing budget context;
- allowed concepts only;
- warning if no valid scope;
- confirm selected assignment;
- post-save receipt: “asignado y enviado a X” or exact remaining blocker.

Do not auto-select a financial classification solely to reduce clicks.

---

# Finance target model

## “Pagos”

Subtasks:
- Listos para corte
- En proceso / comprobante pendiente
- Pagados recientes
- Excepciones

`Payment Run` can remain as expert term/subtitle, but the primary language should state the operational task.

## “CFDI”

Group:
- carga;
- matching;
- SAT status;
- exceptions.

## “Finanzas”

Aggregate:
- cash/obligations;
- payment status;
- cashflow;
- exceptions.

Do not merge AR and AP semantics.

---

# Accounting target model

Primary work:
- **Pendientes de clasificación**
- **Pólizas COI**
- **Conciliación**
- **Cuentas por cobrar / Ingresos**
- **Libros y cierres**

The IA must reconcile the current CxC route-owner conflict before implementation.

---

# Presupuestos target model

Expose only canonical product destinations:
- Presupuestos
- Torneo / proyecto
- Partidas / plan mensual
- Ingresos / CFDI

Legacy/bridge implementation routes must not appear as peer navigation.

---

# Detail-page interaction contract

For Documento, Informe, Payment item, reconciliation item and other critical details:

## Header
- human object type + reference
- human status
- project/tournament
- amount when applicable

## “Qué pasa ahora”
A compact state explanation:
- current queue/owner;
- blocker or required evidence;
- next action;
- who can take it.

## Primary action
At most one visually dominant action for the current actor/context.

Secondary actions remain available but visually subordinate.

## Evidence
- files/CFDI/payment proof;
- approvals;
- budget classification;
- accounting preview;
- audit history.

## Navigation continuity
- return to the originating queue/list;
- preserve search/filter/sort state where safe.

---

# Language policy hypothesis

Prefer task language; retain technical/accounting terms as secondary labels where operators need them.

Examples for validation:
- “Programación de pagos” — subtitle “Payment Run”
- “Comprobante de pago” — avoid unexplained “testigo” if users do not use that term
- “Pendientes de clasificación contable” — module may remain “Limpieza contable”
- “Asignación presupuestal” — workflow owner may remain “Control Presupuestal”

No terminology change should be implemented without user validation.

# Non-goals

- no permission flattening;
- no new write authority;
- no replacement of canonical services;
- no SPA/framework migration as a prerequisite;
- no hiding of audit evidence;
- no conversion of accounting expert screens into generic cards if it reduces efficiency;
- no route renaming until compatibility and links are proven.

# Decision gates before implementation

1. UAT confirms first-click/navigation friction.
2. Current route reachability is reconciled.
3. CxC owner/path conflict resolved.
4. Effective-profile menu snapshots captured.
5. Target IA reviewed by at least one user from each critical role.
6. Changes split into reversible PRs by journey.
