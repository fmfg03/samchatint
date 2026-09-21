# RQF-UX-002 — Navigation & Information Architecture Map

Status: INITIAL_REPOSITORY_SWEEP
Date: 2026-09-21
Baseline: `main@361ad7a2`
Issue: #352

## Scope

This document maps the navigation systems visible in repository evidence. It does not claim the same links are visible to every user: access is affected by role, area, explicit access rules, named catalog-admin exceptions and position-scoped Direction authority.

## Navigation layers found

### Layer 1 — Global top navigation

Repository tests reference `render_top_navigation(...)`. Access is built dynamically from effective visible tools, rather than a single static list.

Confirmed global concepts include at least:
- Panel
- Operaciones
- Gastos / Informes / Solicitudes depending on effective access
- Finanzas / Contabilidad / Presupuestos for authorized users
- Soporte
- Telegram / training support tools
- Direction for explicitly authorized internal positions

### Layer 2 — Gastos workspace navigation

Repository tests reference `_gastos_workspace_nav_html(...)`.

Confirmed peer concepts include:
- Informes de gastos
- Solicitudes de transferencia
- Todos los documentos
- Alta de beneficiarios
- Préstamos (tested as a Gastos navigation item)

This layer mixes object families and supporting catalogs.

### Layer 3 — Accounting subnavigation

Repository tests reference `_contabilidad_subnav(...)`.

Confirmed accounting work includes CxC and repository Finance UAT lists:
- COI
- Cuentas por Cobrar
- Ingresos
- Conciliación
- Diario
- Mayor
- Balanza
- close/accounting status surfaces

### Layer 4 — Admin navigation

Repository tests reference `render_admin_navigation(...)`.

The access-control catalog includes:
- Admin gastos
- Finanzas
- Empleados
- Perfiles
- Proveedores y clientes
- Torneos y proyectos
- Cuentas contables
- Centros de costo
- RFC
- Contabilidad
- Nómina
- Customer Success
- Soporte admin
- Control de accesos
- Estrategias de autorización
- Warnings de autorización

### Layer 5 — Breadcrumbs

Tests verify breadcrumbs across:
- Documentos
- Informes
- Gastos
- Beneficiarios
- AMEX
- CxC
- Executive center
- Presupuestos

Breadcrumb coverage is positive evidence of local context, but it is not yet an inventory proving universal coverage.

### Layer 6 — Module-local tabs

Example:
- `/admin/soporte`
- `/admin/soporte/estado-sistema`

Other modules may have local navigation or filter bars that need route-by-route inventory.

---

# Current conceptual map

```text
PANEL
├── OPERACIONES
│   └── operational console / tournament work
├── GASTOS
│   ├── Informes de gastos
│   │   ├── crear
│   │   ├── detalle
│   │   ├── gastos / CFDI
│   │   └── saldar
│   ├── Solicitudes de transferencia
│   │   ├── tercero/proveedor
│   │   ├── personal
│   │   └── anticipo
│   ├── Todos los documentos
│   ├── Beneficiarios
│   └── Préstamos
├── WORK QUEUES
│   ├── Aprobaciones pendientes
│   ├── Control Presupuestal
│   └── Pagos pendientes
├── FINANZAS / ADMIN GASTOS
│   ├── Finance command center
│   ├── Expenses
│   ├── Invoices / CFDI
│   ├── SAT / matching
│   ├── Payment Run
│   ├── Cashflow
│   ├── CxC read model / workbench
│   └── Limpieza contable
├── CONTABILIDAD
│   ├── COI
│   ├── CxC vista contable / ingresos
│   ├── Conciliación
│   ├── Diario
│   ├── Mayor
│   └── Balanza
├── PRESUPUESTOS
│   ├── dashboard
│   ├── torneo
│   └── ingresos / CFDI
├── DIRECCIÓN
│   ├── tableros
│   └── reportes
└── SOPORTE
    ├── mis tickets
    └── admin soporte
```

This tree is conceptual only. It highlights that “work queues” are stages of objects that also live under Gastos/Finance domains.

---

# Structural usability risks to validate

## NAV-01 — Module-first entry can force users to know the system taxonomy

A user goal is often “aprobar lo que me toca”, “pagar lo aprobado”, “corregir mi informe”, or “resolver excepciones”, not “entrar a Documentos/Finanzas/Contabilidad”.

Repository structure currently exposes both domain-first and stage-first destinations.

**Validation task:** start users on `/panel` and ask them to complete work without giving route/module names.

## NAV-02 — Multiple Finance umbrellas

Visible concepts can include “Admin gastos”, “Administración financiera”, “Contabilidad”, “Presupuestos” and “Limpieza contable”.

**Risk:** choice overload or wrong-module entry.

**Validation:** first-click test with Finance users for:
- “quiero pagar lo aprobado”
- “quiero clasificar un gasto”
- “quiero ver qué se cobró”
- “quiero revisar presupuesto vs real”

## NAV-03 — Solicitud creation route families are split

List and create surfaces are not uniformly grouped:
- `/gastos-terceros`
- `/documentos/nueva-solicitud-terceros`
- `/documentos/nueva-solicitud-personal`
- `/gastos-terceros/solicitar-anticipo`

**Risk:** users must learn implementation distinctions.

## NAV-04 — Queue names assume workflow knowledge

“Aprobaciones pendientes”, “Control Presupuestal”, “Pagos pendientes”, “Limpieza contable” and “Payment Run” are meaningful to expert users but do not all answer:
- why is this item here?
- what action is expected?
- what happens next?

The target architecture should preserve expert terminology where required but add task/action language.

## NAV-05 — Effective access can make menus differ substantially between users

Current authority model includes:
- role defaults;
- explicit area rules;
- named catalog admins;
- Direction position/portfolio scope.

**Requirement:** empty/missing menu items must not feel like broken navigation. Users need stable primary landmarks even when tools are hidden.

## NAV-06 — Canonical vs legacy implementation must be invisible to normal users

Presupuestos route policy explicitly contains canonical owners, bridges and legacy candidates. The UI must present one product concept, not repository history.

## NAV-07 — CxC exposes two route contexts

Current repository evidence confirms both routes. `/admin/finanzas/cuentas-por-cobrar` is the canonical Finance AR read model/workbench; it explicitly links to `/admin/contabilidad/cuentas-por-cobrar` as “Vista contable”.

**Risk:** two screens named Cuentas por Cobrar can look like duplicates unless their purpose is explicit.

**Validation:** ask Finance and Contabilidad users which view they expect for portfolio/collections work versus accounting classification/entry work, and verify that cross-links preserve context.

---

# Proposed target navigation model — hypothesis for UAT

Do not implement yet.

## Primary level: role-oriented “Trabajo”

Every user gets a stable first destination:

- **Inicio / Mi trabajo**
- **Buscar**
- domain tools they are allowed to access
- **Soporte**

“Mi trabajo” should aggregate counts and deep links, not duplicate business state.

Examples:

### Solicitante
- Borradores por terminar
- Rechazados por corregir
- Informes abiertos
- Esperando aprobación
- Pagados / cerrados recently

### Aprobador
- Requieren mi decisión
- Devueltos/corregidos
- Historial reciente

### Control Presupuestal
- Sin partida
- Excepciones de alcance
- Recién asignados

### Finanzas/Tesorería
- Aprobados listos para corte
- En proceso sin comprobante
- Excepciones de pago
- Conciliación por revisar

### Contabilidad
- Limpieza pendiente
- COI listo para revisar/exportar
- Conciliaciones pendientes
- CxC/ingresos con excepción

### Dirección
- Requiere atención
- Torneos/portfolios
- Finanzas/Operación resumida
- Reportes

## Secondary level: domain navigation

Keep expert modules for power users:
- Gastos
- Finanzas
- Contabilidad
- Presupuestos
- Operaciones
- Configuración

But do not require domain selection to discover assigned work.

## Detail-page standard

Every business-object detail should expose:
1. human status;
2. “qué significa”;
3. current owner/queue;
4. next expected action;
5. primary action;
6. evidence/history;
7. consistent return path preserving prior filters.

# Evidence required before adopting target IA

- first-click tests by role;
- route reachability map;
- task completion baseline;
- counts of duplicate/ambiguous entry points;
- UAT on representative real cases;
- confirmation that no authorization boundary is weakened by aggregation.
