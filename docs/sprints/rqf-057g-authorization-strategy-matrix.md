# RQF-057G1 - Authorization strategy matrix foundation

Status: CLOSED_LOCAL_PENDING_COMMIT
Scope: first safe slice for customer authorization matrix.

## Decision

Do not put business approval strategy inside `access_control_rules` directly.
The existing Control de accesos module governs module visibility/actions by role
and area. Authorization strategy answers a different question: who must approve
a business document based on area, erogation type, amount, and exception flags.

This slice therefore adds a pure resolver service and registers the future
board as a governable Control de accesos tool:

- Tool key: `configuracion.estrategias_autorizacion`
- Path: `/admin/estrategias-autorizacion`
- Actions: `ver`, `editar`, `administrar`
- Default access: superadmin only

## Canonical role mapping supplied by Francisco

| Business role | Resolver key | Employee matcher |
| --- | --- | --- |
| DG | `dg` | `federico gonzalez` |
| DAyF | `dayf` | `luis angel orozco`, `luis angel` |
| DGoat | `dgoat` | `olof` |
| Director de Operaciones | `director_operaciones` | `odilon trujillo`, `jose odilon trujillo` |
| Gerente de AyF | `gerente_ayf` | `benjamin jimenez`, `benjamin` |
| Director de AyF | `director_ayf` | `luis angel orozco`, `luis angel` |

Production data audit on 2026-07-30 found active rows for Benjamin, Federico
Gonzalez y Vega, Jose Odilon Trujillo Macedo, Luis Angel Orozco Colin, and Olof.
Only one Federico row matched the current production employee table; the matcher
is deliberately role-level so a second Federico can match when present.

## Implemented resolver coverage

The resolver currently covers the customer matrix's core categories for:

- Operaciones
  - supplier transfers up to / above 100k
  - travel expense reports
  - tournament advances, including pending-advance OR escalation
  - tournament reimbursements
  - urgent payments up to / above 25k
  - extra operation costs
  - unbudgeted costs
  - no-deductible / no-invoice costs
  - budget excess
- Comunicaciones / RRSS / Patrocinios
  - supplier transfers up to / above 10k
  - travel reports up to / above 10k
  - tournament advances above 10k or pending
  - no-deductible / no-invoice costs
- Administracion
