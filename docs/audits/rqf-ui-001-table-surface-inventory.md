# RQF-UI-001 table surface inventory

Date: 2026-09-14

This inventory describes repository coverage for the active
`copa_telmex_dashboard.py` runtime. It is implementation evidence, not
authenticated UAT or production acceptance.

| Surface / route family | Code owner | Classification | Action in this change | Evidence |
| --- | --- | --- | --- | --- |
| `/documentos/pendientes`, `/informes-de-gastos`, transfer-request summary and user Gastos workspaces | `src/devnous/gastos/routes/user_routes.py` | `SHARED_TABLE_SHELL` | Shared bounded viewport, sticky headers, persistent horizontal scrollbar; semantic status/value/action columns keep complete content | Contract tests plus existing document workflow regressions |
| Admin and canonical Presupuestos routes | `src/devnous/gastos/routes/admin_routes.py`, `admin_budget_routes.py` | `SHARED_TABLE_SHELL` | Shared bounded viewport, sticky headers and complete action labels | Contract tests plus existing domain regressions |
| Finance command center `/admin/finanzas` | `src/devnous/gastos/routes/admin_routes.py` | `SHARED_TABLE_SHELL` | Six operational `finance-table` elements migrated into `.table-shell` | Contract test plus authenticated visual UAT pending |
| Support routes using `_workspace_shell_styles` | `src/devnous/gastos/routes/support_routes.py` | `SHARED_TABLE_SHELL` and `LEGACY_INLINE_TABLE` | Shared shells inherit the contract; inline tables remain listed below | Source inventory; authenticated visual UAT pending |
| Runtime artifact index | `src/samchat/artifacts/admin_ui.py` | `DOMAIN_TABLE_SHELL` | Four tables migrated to `.table-shell` | Contract test |
| Cashflow monthly view | `src/samchat/cashflow/admin_ui.py` | `DOMAIN_TABLE_SHELL` | Monthly table migrated to `.table-shell` | Contract test |
| Cuentas por Cobrar | `src/samchat/ar/admin_ui.py` | `DOMAIN_TABLE_SHELL` | Existing `.ar-table-wrap` already supplies bounded two-axis scrolling and sticky headers | Existing AR tests; authenticated visual UAT pending |
| Internal Direction dossier | `src/samchat/client_executive/ui.py` | `DOMAIN_TABLE_SHELL` | Existing `.table-wrap` strengthened with bounded scrolling, sticky headers and stable scrollbar gutter | Contract test; Direction UAT pending |
| Budget spreadsheet-like grids | `src/devnous/gastos/routes/admin_budget_ui.py` | `SPECIALIZED_GRID` | No generic override; existing fixed/wide column behavior preserved | Explicit exception; dedicated browser UAT required |
| Registration review asset grid | `templates/registration_review_detail.html` | `SPECIALIZED_GRID` | No generic override because it is not a standard operational table | Explicit exception |
| Legacy Teams and Players templates | `templates/teams.html`, `templates/players.html`, `templates/team_detail.html` | `LEGACY_INLINE_TABLE` | Not connected to the shared workspace contract in this change | `PENDING_HUMAN_ASSIGNMENT`; migration follow-up required if still runtime-reachable |
| Legacy Support inline tables | `src/devnous/gastos/routes/support_routes.py` | `LEGACY_INLINE_TABLE` | Not altered without route-by-route evidence | `PENDING_HUMAN_ASSIGNMENT`; visual review required |
| Reporting compatibility routes | `src/devnous/gastos/routes/client_reporting_routes.py` | `LEGACY_INLINE_TABLE` | Not altered; temporary compatibility surface | `PENDING_HUMAN_ASSIGNMENT`; Direction replacement/retirement decision required |

## Coverage statement

The shared contract now covers the primary active Gastos, Operaciones,
Finance, Presupuestos, Support and admin pages that render tables inside
`.table-shell`. Artifacts, Cashflow and Direction adopt an equivalent bounded
domain shell. This change does **not** claim complete coverage of specialized
grids or legacy inline tables.

## Visual evidence still required

Before `business_accepted`, preserve authenticated screenshots at 1440, 1280,
1024, 768 and 390 CSS pixels for:

- `/documentos/pendientes` with at least 30 rows and both row actions;
- one Finance table;
- one canonical Presupuestos table plus one specialized budget grid;
- one Direction dossier table;
- one Support table;
- keyboard focus and browser zoom at 200 percent.

## Canon

`Canon unchanged`: this change modifies presentation and accessibility of
existing read/action surfaces. It does not change routes, HTTP methods, fields,
authority, financial state, persistence, audit behavior, or acceptance claims.
