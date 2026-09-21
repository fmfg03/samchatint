# RQF-UX-002 — Surface Inventory

Status: INITIAL_REPOSITORY_SWEEP
Date: 2026-09-21
Issue: #352
Baseline: `main@c53a6591`
Purpose: map user-relevant live-web surfaces to tasks and identify IA/UX debt. This is not production acceptance.

## Classification

- `PRIMARY_WORKSPACE`: main entry for a work domain.
- `TASK_QUEUE`: items requiring action.
- `DETAIL`: single business object/context.
- `FORM`: creation/edit/action form.
- `DASHBOARD`: aggregate/read model.
- `ADMIN_CONFIG`: configuration/catalog authority.
- `LEGACY`: retained compatibility/reference candidate.
- `SPECIALIZED_GRID`: high-density grid requiring dedicated behavior.
- `EXPORT`: generated artifact, not a primary workspace.

## Core user surfaces

| Surface | Class | User intent | Repository owner/evidence | Initial UX note |
| --- | --- | --- | --- | --- |
| `/panel` | PRIMARY_WORKSPACE | General entry | access-control `panel.home` | Must be tested as actual starting point by role |
| `/panel/operaciones-console` | DASHBOARD / PRIMARY_WORKSPACE | Operational console | `panel.operaciones` | Separate operational entry from Gastos/Finance |
| `/gastos-terceros` | PRIMARY_WORKSPACE | Transfer requests | `gastos.solicitudes` | Overlaps conceptually with Documentos |
| `/documentos/nueva-solicitud-terceros` | FORM | New third-party/provider request | support route manifest + user routes | Creation lives under another route family than list |
| `/documentos/nueva-solicitud-personal` | FORM | New personal request | support route manifest + user routes | Same structural split as above |
| `/gastos-terceros/solicitar-anticipo` | FORM | Advance request | request tests/docs | Variant naming requires UAT |
| `/informes-de-gastos` | PRIMARY_WORKSPACE | Expense reports | `gastos.informes` | Core employee flow |
| `/informes-de-gastos/crear` | FORM | New expense report | finance UAT | Clear domain path |
| `/informes-de-gastos/{id}` | DETAIL | Report context/actions | finance UAT | High-value context screen |
| `/informes-de-gastos/{id}/gastos/quick` | FORM | Quick expense capture | finance UAT | Needs evidence/error usability review |
| `/informes-de-gastos/{id}/gastos/amex` | FORM | Mark AMEX expenses | finance UAT | Specialized business variant |
| `/informes-de-gastos/{id}/saldar` | FORM | Settlement / reimbursement / return | finance UAT | Terminology must match direction of money |
| `/documentos/todos` | PRIMARY_WORKSPACE / SEARCH | Cross-document search | user routes/tests | Powerful but overlaps owner/domain lists |
| `/documentos/{id}` | DETAIL | Unified document detail | finance UAT | Should be canonical explanation of state/next step |
| `/documentos/pendientes` | TASK_QUEUE | Approval work | support route manifest | Known historical UI friction; critical queue |
| `/documentos/historial-aprobador` | DASHBOARD / HISTORY | Approval history | support route manifest | Could require context switching from detail |
| `/documentos/control-presupuestal` | TASK_QUEUE | Budget assignment | route/tests | High-risk classification queue |
| `/documentos/pendientes-pago` | TASK_QUEUE | Individual payment work | support route manifest | Semantically adjacent to Payment Run |

## Finance / accounting surfaces

| Surface | Class | User intent | Evidence | Initial UX note |
| --- | --- | --- | --- | --- |
| `/admin/gastos` | PRIMARY_WORKSPACE / DASHBOARD | Admin gastos / accounting view | access-control + support manifest | One of several Finance-like hubs |
| `/admin/gastos/expenses` | DASHBOARD | Global expenses | access-control | Administrative table |
| `/admin/gastos/invoices` | DASHBOARD | CFDI/admin invoices | access-control | Separate from SAT/matching/COI |
| `/admin/gastos/cfdis/carga-masiva` | FORM | Bulk CFDI import | access-control / Finance UAT | Specialized ingestion |
| `/admin/gastos/cfdis/matching` | TASK_QUEUE | CFDI matching | access-control / Finance UAT | Exception-driven task |
| `/admin/gastos/sat` | DASHBOARD / ADMIN_CONFIG | SAT credentials/jobs | access-control / Finance UAT | Technical vocabulary likely appropriate only to finance/admin |
| `/admin/gastos/sin-cuenta-contable` | TASK_QUEUE | Accounting cleanup | finance UAT | Recommended first Finance review surface |
| `/admin/finanzas` | PRIMARY_WORKSPACE / DASHBOARD | Finance command center | access-control | Competes with `/admin/gastos` and `/admin/contabilidad` as “home” |
| `/admin/finanzas/payment-run` | TASK_QUEUE / WORKSPACE | Payment cutoff | engineering canon / tests | Critical state semantics |
| `/admin/finanzas/payment-run/closures/{id}` | DETAIL / HISTORY | Cutoff evidence | finance UAT | Should explain cutoff vs payment |
| `/admin/finanzas/cashflow` | DASHBOARD | Cashflow planning | engineering canon | Read-model semantics differ from actual cash |
| `/admin/finanzas/cuentas-por-cobrar` | PRIMARY_WORKSPACE / DASHBOARD | Canonical Finance AR read model, billing schedule, gaps, matching | AR route contract + engineering canon | Links explicitly to Accounting “Vista contable” |
| `/admin/finanzas/cuentas-por-cobrar/item/{id}` | DETAIL | AR item detail | AR route contract | Canonical Finance AR detail |
| `/admin/contabilidad` | PRIMARY_WORKSPACE | Accounting domain | access-control | Broad umbrella |
| `/admin/contabilidad/coi` | TASK_QUEUE / DASHBOARD | COI policies | finance UAT | Accounting power-user surface |
| `/admin/contabilidad/coi/{id}` | DETAIL | Policy detail | finance UAT | Drill-down |
| `/admin/contabilidad/conciliacion` | TASK_QUEUE / WORKSPACE | Bank reconciliation | finance UAT | Candidate match semantics must stay explicit |
| `/admin/contabilidad/conciliacion/{id}` | DETAIL | Review movement | finance UAT | Evidence-heavy decision |
| `/admin/contabilidad/conciliacion/auditoria` | HISTORY | Reconciliation audit | finance UAT | Separate audit surface |
| `/admin/contabilidad/cuentas-por-cobrar` | WORKSPACE | Accounting-context CxC / CFDI-income operations | finance UAT + accounting route tests | Separate operational/accounting view; Finance AR UI links here as “Vista contable” |
| `/admin/contabilidad/ingresos` | TASK_QUEUE / HISTORY | Collections/income | finance UAT | Needs relation to CxC made explicit |
| `/admin/contabilidad/diario/*` | DASHBOARD / EXPORT | Journal | runtime artifact docs | Power-user accounting |
| `/admin/contabilidad/mayor/*` | DASHBOARD / EXPORT | Ledger | runtime artifact docs | Power-user accounting |
| `/admin/contabilidad/balanza/*` | DASHBOARD / EXPORT | Trial balance | runtime artifact docs | Power-user accounting |

## Presupuestos

| Surface | Class | User intent | Evidence | Initial UX note |
| --- | --- | --- | --- | --- |
| `/admin/presupuestos` | PRIMARY_WORKSPACE | Budget dashboard | canonical route policy | Canonical owner |
| `/admin/presupuestos/torneo/{key}` | DETAIL / SPECIALIZED_GRID | Tournament budget | canonical route policy | Spreadsheet-like UX needs dedicated UAT |
| `/admin/presupuestos/torneo/{key}/cfdi-ingresos` | TASK_QUEUE | Link income CFDI | route policy | Crosses budget + AR concepts |
| `/admin/presupuestos-legacy` | LEGACY | Historical budget view | route policy | Candidate hide/reference; must not be extended |
| bridge handlers in `admin_routes.py` | LEGACY / BRIDGE | Support canonical UI | route policy | Implementation detail should not leak into navigation |

## Dirección

| Surface | Class | User intent | Evidence | Initial UX note |
| --- | --- | --- | --- | --- |
| `/direccion/tableros` | DASHBOARD / PRIMARY_WORKSPACE | Executive overview | engineering canon | Position-scoped, read-only |
| `/direccion/reportes` | DASHBOARD / HISTORY | Executive reports | engineering canon | Must avoid operational taxonomy overload |

## Soporte

| Surface | Class | User intent | Evidence | Initial UX note |
| --- | --- | --- | --- | --- |
| `/soporte` | PRIMARY_WORKSPACE | My tickets | support_routes module contract | Simple self-service flow |
| `/soporte/nuevo` | FORM | New ticket | support_routes | Good task-specific route |
| `/soporte/{id}` | DETAIL | Ticket conversation | support_routes | Direct continuity |
| `/admin/soporte` | TASK_QUEUE | Staff ticket triage | support_routes | Admin-only |
| `/admin/soporte/estado-sistema` | DASHBOARD | System blockers/status | support_routes | Contains a route visibility manifest separate from access-control catalog |

## Configuration / catalogs

The access-control catalog exposes additional admin surfaces:
- `/admin/empleados`
- `/admin/perfiles`
- `/admin/proveedores-clientes`
- `/admin/torneos`
- `/admin/cuentas-contables`
- `/admin/centros-costo`
- `/admin/rfc`
- `/admin/control-accesos`
- `/admin/estrategias-autorizacion`
- `/admin/estrategias-autorizacion/warnings`
- `/admin/nomina`
- `/admin/customer-success`
- `/assistant`

These are not all part of a normal user's primary journey, but their navigation visibility affects admin cognitive load.

## Existing table/UI classification inherited from RQF-UI-001

The previous inventory already distinguishes:
- shared table shell;
- domain table shell;
- legacy inline tables;
- specialized grids.

Known debt from that inventory:
- legacy Teams/Players templates;
- legacy Support inline tables;
- reporting compatibility routes;
- specialized budget grids requiring dedicated browser UAT.

PR #351 is merged in this baseline and its full-width/data-layout contract is repository current; authenticated browser acceptance remains separate evidence.

---

# Information-architecture findings

## IA-01 — Finance has multiple plausible “home” locations

A Finance user can encounter:
- Vista contable / Admin gastos
- Administración financiera
- Contabilidad
- Limpieza contable
- Presupuestos
- Documentos/Pagos

This is repository-confirmed structure. Whether users find it confusing is `HYPOTHESIS` pending task UAT.

## IA-02 — Creation and listing are not always in the same route family

Example:
- list: `/gastos-terceros`
- create: `/documentos/nueva-solicitud-terceros`
- another request variant: `/gastos-terceros/solicitar-anticipo`

This increases the importance of consistent labels and task-based navigation.

## IA-03 — Object type and workflow stage are both used as navigation concepts

Examples:
- “Solicitudes de transferencia” = object/work type
- “Aprobaciones pendientes” = workflow stage
- “Control Presupuestal” = workflow gate
- “Pagos pendientes” = workflow stage
- “Todos los documentos” = cross-object search

A role-oriented home could reduce the need to understand this taxonomy before acting.

## IA-04 — Legacy and canonical route concepts coexist

Presupuestos explicitly documents canonical, bridge, legacy and candidate-removal routes. The usability audit must verify that legacy implementation surfaces are not presented as peer destinations.

## IA-05 — CxC has two confirmed surfaces with different purposes

Current repository evidence confirms both:
- `/admin/finanzas/cuentas-por-cobrar`: canonical Finance AR read model/workbench, including billing schedule, actionable gaps, accepted collection matching and exports;
- `/admin/contabilidad/cuentas-por-cobrar`: accounting-context CxC surface, linked from the Finance AR UI as “Vista contable”.

This is not a missing-route conflict. It is an information-architecture question: the product must make the relationship and intended use of both views obvious.

# Next sweep

1. Reconcile live route registration for every row above.
2. Render navigation for representative effective-access profiles.
3. Record which surfaces are reachable through UI versus URL-only.
4. Identify duplicate labels pointing to different route families.
5. Identify routes with no breadcrumb / no clear return path.
6. Convert this inventory from repository-level to authenticated runtime evidence.
