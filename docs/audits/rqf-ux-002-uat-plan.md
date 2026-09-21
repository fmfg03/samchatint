# RQF-UX-002 — Task-based UAT Plan

Status: READY_FOR_AUTHENTICATED_UAT
Date: 2026-09-21
Issue: #352
Baseline repository: `main@361ad7a2`

## Purpose

Measure whether users can complete critical SamChat work without being taught the product taxonomy.

This UAT is distinct from functional correctness testing. Existing Finance UAT cases remain authoritative for accounting/financial correctness; this plan adds task usability measurements.

## Rules

1. Do not tell the participant which menu/module/route to use.
2. Use a realistic case with safe/test data.
3. Do not coach unless the participant is blocked.
4. When help is given, record the exact prompt.
5. Record the route sequence, not only the final result.
6. Do not infer success from “the item disappeared”.
7. Financial/state mutations require authorized test context.
8. Production writes are out of scope until separately approved.

## Measures per task

Required:
- task success: `PASS / PASS_WITH_HELP / FAIL`
- start route
- end route/state
- elapsed time
- clicks/taps
- backtracks
- wrong destinations opened
- validation errors
- business errors
- help requests
- terms the participant asks about
- final confidence: participant can state what happened and what comes next
- screenshot/recording evidence id

Optional:
- first-click destination
- time to first correct action
- number of filter changes
- scroll distance / horizontal scroll use
- keyboard-only outcome
- 200% zoom outcome
- mobile/touch outcome

## Participants / effective profiles

Minimum profiles:
1. Empleado / solicitante.
2. Aprobador.
3. Control Presupuestal operator.
4. Finanzas / Payment Run operator.
5. Contabilidad.
6. Dirección read-only.

Because access is not purely role-based, each session must record:
- employee id/reference used for test;
- nominal role;
- area;
- effective visible tool set or profile fixture;
- Direction position/portfolio when applicable.

## Test tasks

### T01 — New provider/third-party transfer

Prompt:
> Necesitas solicitar una transferencia a un proveedor/tercero con la información del caso. Haz la solicitud y déjala en el punto correcto para que continúe el proceso.

Observe:
- first menu choice;
- request-type confusion;
- beneficiary/account discovery;
- project/reference fields;
- send/submit distinction;
- post-submit understanding.

Target journey: J01.

### T02 — New advance

Prompt:
> Necesitas un anticipo para una actividad próxima. Solicítalo para el beneficiario indicado y confirma qué debe pasar después.

Observe whether “anticipo” versus other request types is discoverable.

Target: J02.

### T03 — Open expense report and capture evidence

Prompt:
> Crea un informe de gastos para este caso y registra el gasto usando los comprobantes proporcionados.

Observe:
- entry;
- project/beneficiary;
- XML/PDF handling;
- terminology;
- validation errors;
- save state.

Targets: J03/J04.

### T04 — Correct rejected report

Prompt:
> Este informe fue rechazado. Encuentra por qué, corrígelo y déjalo nuevamente listo para continuar.

Success requires participant to explain:
- rejection reason;
- changed field/evidence;
- resulting state;
- next owner/action.

Target: J05.

### T05 — Find current status

Prompt:
> Dime dónde está la solicitud X, quién debe actuar ahora y qué falta para terminarla.

No route hint.

Target: J06.

### T06 — Approve one item

Prompt:
> Tienes una solicitud pendiente. Revisa si tiene contexto suficiente y apruébala.

Observe:
- queue discovery;
- information sufficiency;
- confidence;
- accidental-action risk;
- success feedback.

Target: J07.

### T07 — Reject with reason

Prompt:
> Esta solicitud no debe avanzar porque falta la evidencia indicada. Recházala dejando claro qué debe corregir el solicitante.

Observe rejection reason visibility to both sides.

Target: J07/J05.

### T08 — Reconstruct approval history

Prompt:
> Averigua quién aprobó/rechazó este documento y cuándo.

Target: J08.

### T09 — Budget assignment

Prompt:
> Este documento está detenido por presupuesto. Asigna la partida/concepto correcto para el caso y confirma que avanzó.

Observe:
- discovery of Control Presupuestal;
- option scoping;
- wrong-choice prevention;
- post-save feedback.

Target: J09.

### T10 — Find approved items ready for payment

Prompt:
> Identifica qué solicitudes aprobadas están listas para entrar al siguiente corte de pagos.

Target: J10.

### T11 — Close Payment Run cutoff

Prompt:
> Prepara el corte con los pagos indicados y ciérralo. Después explica cuáles están pagados y cuáles no.

Critical acceptance: participant must not interpret cutoff closure as proof of payment.

Target: J11.

### T12 — Confirm payment with proof

Prompt:
> Registra el comprobante de este pago y confirma que ahora sí quedó pagado.

Target: J12.

### T13 — Accounting cleanup

Prompt:
> Encuentra por qué este gasto no está listo para COI, corrige lo necesario y verifica que ya pueda continuar.

Target: J13.

### T14 — COI review/export

Prompt:
> Revisa la prepóliza del caso y genera el archivo que usarías para revisión/carga en COI.

Target: J14.

### T15 — Bank reconciliation

Prompt:
> Revisa este movimiento bancario, determina si existe evidencia suficiente para conciliarlo y registra la decisión autorizada.

Observe distinction between candidate and accepted match.

Target: J15.

### T16 — Accounts receivable

Prompt:
> Encuentra esta factura emitida, identifica a qué torneo/partida pertenece y verifica su estado de cobranza.

Target: J16.  
Precondition: resolve actual registered CxC route before session.

### T17 — Budget versus actual

Prompt:
> Para este torneo, encuentra el presupuesto, el ejecutado/actual y la partida indicada.

Target: J17.

### T18 — Direction attention scan

Prompt:
> Sin entrar a módulos operativos uno por uno, dime cuáles son los tres asuntos que requieren atención y abre la evidencia de uno.

Target: J18.

### T19 — Create support ticket

Prompt:
> Reporta este problema de uso y luego dime dónde revisarías su avance.

Target: J19.

### T20 — Support triage

Prompt:
> Encuentra el ticket indicado, clasifícalo, asígnalo y deja una respuesta/resolución según el caso.

Target: J20.

## Viewport/accessibility passes

Run a focused subset (T05, T06, T09, T11, T13) at:
- desktop 1440px;
- desktop 1280px;
- 1024px;
- 768px;
- mobile 390px;
- browser zoom 200%;
- keyboard-only.

After PR #351 is merged/deployed, rerun table-heavy tasks to compare against the prior RQF-UI-001 defect class.

## Observation vocabulary

Record one or more:
- `WRONG_FIRST_CLICK`
- `MENU_SEARCH`
- `TERM_UNKNOWN`
- `STATUS_AMBIGUOUS`
- `NEXT_STEP_AMBIGUOUS`
- `MISSING_CONTEXT`
- `FILTER_LOST`
- `BACKTRACK`
- `SCROLL_FRICTION`
- `ACTION_RISK`
- `ERROR_NOT_ACTIONABLE`
- `SUCCESS_NOT_CONFIRMED`
- `HELP_REQUIRED`

## Acceptance for audit phase

The audit has enough UAT evidence when:
- every critical role has at least one session;
- J01–J18 have at least one measured run where authority/data allow it;
- high-risk queues J07/J09/J11/J13/J15 have evidence with realistic records;
- route conflicts such as CxC are reconciled;
- top friction points are reproducible;
- proposed IA changes can be tied to measured failures rather than aesthetic preference.
