# RQF-UX-002B — Effective-profile navigation baseline

Status: SIMULATED_BROWSER_EVIDENCE
Date: 2026-09-21
Parent: #352
Issue: #357
Implementation: PR #359

## Evidence boundary

This baseline uses synthetic effective-access profiles rendered through the real
SamChat navigation helpers inside the isolated Playwright test app. It is not
human UAT and does not claim that a person will choose the same first click.

The task prompt does not reveal the target module name.

## Profiles

| Profile | Task prompt | Navigation evidence |
| --- | --- | --- |
| Employee | Solicitar una transferencia a un proveedor | Direct task entry `/gastos-terceros` is visible and keyboard reachable |
| Approver | Atender una solicitud que requiere aprobación | `/documentos/pendientes` is not exposed by the navigation helpers; actual `/panel` task-card behavior must be simulated before classifying this as a product defect |
| Control Presupuestal | Resolver un documento detenido por asignación presupuestal | `/documentos/control-presupuestal` is not exposed by the navigation helpers; actual `/panel` task-card behavior must be simulated before classifying this as a product defect |
| Finance | Preparar el siguiente corte de pagos | Finance entry `/admin/finanzas` is visible; the admin shell also exposes `Payment Run` |
| Accounting | Resolver un gasto que no está listo para COI | Canonical accounting entry in the rendered navigation is `/admin/contabilidad/estado`; accounting subnavigation exposes COI, CxC, CxP, reclasificación, conciliación and related tools |
| Direction | Identificar qué requiere atención en Dirección | Effective access includes `direccion.tableros_ejecutivos`, but primary navigation does not expose `/direccion/tableros` or `/direccion/reportes`; tracked as #361 |

## First diagnostic run

Browser trace inspection confirmed the following href sets.

### Employee

Relevant hrefs:
- `/panel`
- `/informes-de-gastos`
- `/gastos-terceros`
- `/prestamos`
- `/documentos/todos`
- `/beneficiarios/altas`

The task “Solicitar una transferencia a un proveedor” has a direct navigation entry.

### Approver

Relevant hrefs:
- `/panel`
- `/informes-de-gastos`
- `/gastos-terceros`
- `/prestamos`
- `/documentos/todos`
- `/beneficiarios/altas`

Not exposed by these navigation helpers:
- `/documentos/pendientes`

Repository contract tests already show that the real panel conditionally adds the approval inbox for assigned approvers. Therefore the next evidence step is to simulate the real panel card, not to add a navigation link based on this result.

### Control Presupuestal

Relevant hrefs include:
- `/panel`
- `/gastos-terceros`
- `/documentos/todos`
- `/admin/finanzas`
- `/admin/finanzas/payment-run`
- `/admin/gastos/sin-cuenta-contable`
- `/admin/contabilidad/estado`

Not exposed:
- `/documentos/control-presupuestal`

As with Approver, this remains `PANEL_SIMULATION_REQUIRED`, not a confirmed UX defect.

### Finance

Relevant direct/admin entries include:
- `/admin/finanzas`
- `/admin/finanzas/payment-run`
- `/admin/finanzas/cashflow`
- `/admin/finanzas/cuentas-por-cobrar`
- `/admin/gastos/sin-cuenta-contable`

The task “Preparar el siguiente corte de pagos” has a plausible expert entry through Finance and an explicit Payment Run entry in the admin shell.

### Accounting

The first diagnostic expectation used `/admin/contabilidad`; trace evidence shows the rendered canonical navigation target is `/admin/contabilidad/estado`.

Relevant entries include:
- `/admin/contabilidad/estado`
- `/admin/contabilidad/coi`
- `/admin/contabilidad/conciliacion`
- `/admin/contabilidad/cuentas-por-cobrar`
- `/admin/contabilidad/cuentas-por-pagar`
- `/admin/contabilidad/reclasificacion`
- `/admin/gastos/sin-cuenta-contable`

This was a test expectation correction, not a product defect.

### Direction

Observed primary hrefs:
- `/panel`
- `/admin/presupuestos`
- support/account links

Not present:
- `/direccion/tableros`
- `/direccion/reportes`

This is a confirmed navigation/discoverability gap for the simulated effective profile and is tracked in #361.

## Harness correction

The first 390px profile-page overflow result measured `406px` document width on a `390px` viewport. The extra 16px came from the browser default `body` margin in the test-only wrapper, not from SamChat navigation. The wrapper now explicitly uses `body { margin: 0; }`.

This prevents a test-harness artifact from being promoted as a product defect.

## Next evidence step

1. Simulate the real `/panel` task cards for assigned approver and Control Presupuestal profiles.
2. Preserve the direct-entry regression checks for Employee, Finance and Accounting.
3. Keep Direction as a strict known gap until #361 is fixed.
4. Human UAT remains required to measure actual first click, hesitation, terminology comprehension and confidence.