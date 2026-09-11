# Direction executive compatibility debt

Date: 2026-09-11

## Current semantic boundary

The executive boards and Direction reports are internal Plataforma Sports
surfaces. They are authorized by an active internal identity, an eligible
organizational position, and assigned portfolio/tournament scope. They are not
an external customer portal and do not authorize from the `cliente` role.

Eligible initial positions are `direccion_general`,
`direccion_administracion_finanzas`, `direccion_goat`, and
`director_operaciones`. A superadmin can supervise active portfolios; an
`admin` has no global access merely from that role.

## Deferred physical rename

The following physical names are retained for this bounded correction because
renaming them would require a separate migration, compatibility audit, and
rollback plan:

- `samchat.client_executive` package;
- `samchat.client_reporting` package;
- `client_executive_*` tables;
- `client_report_*` tables;
- route module filenames that retain `client_*` for import compatibility.

The primary public routes are `/direccion/tableros` and
`/direccion/reportes`. Legacy `/cliente/*` and `/admin/reportes-cliente` GET
routes are temporary 307 redirects only; no legacy write route is retained.

## Exit criteria for the follow-up

1. Inventory application imports, SQL, dashboards, external bookmarks, and
   deployment scripts that reference the legacy names.
2. Approve a separately versioned migration and rollback plan.
3. Introduce the new physical names with backward-compatible reads/writes,
   then migrate consumers.
4. Remove temporary redirects only after usage evidence and human approval.
