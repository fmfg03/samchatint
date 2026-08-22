# Presupuestos Route Policy

Status: C4 route-owner policy
Date: 2026-08-21
Scope: route inventory and route-owner policy only. No schema, permission,
import, export workbook format, or mutation behavior changes.

## Operating Rule

`src/devnous/gastos/routes/admin_budget_routes.py` is the canonical route owner
for the live Presupuestos dashboard, tournament detail, income budget, CFDI
income, general budget workbook export, and copy-forward budget workflows.
Version and line mutation routes are also owned by this module.

`src/devnous/gastos/routes/admin_routes.py` may still contain bridge action
handlers used by canonical Presupuestos UI forms, plus legacy candidates that
need dependency validation before removal. Those handlers are not source
authority for new AR, cashflow, planning, or assistant finance behavior.

## Policy Classes

| class | meaning | allowed next work |
|---|---|---|
| `canonical_owner` | Route is owned by the canonical Presupuestos module. | Extend only through the owning service/UI contract and tests. |
| `bridge_required_by_canonical_ui` | Handler still lives in `admin_routes.py` but supports canonical UI form targets or generated exports. | Keep callable until route-target validation proves it can move, hide, redirect, or be removed. |
| `legacy_reference` | Historical route or view retained for comparison or migration. | Do not extend for new finance lanes. |
| `candidate_hide` | Visible legacy entrypoint that may be hidden later. | Requires route-target and navigation validation before UI changes. |
| `candidate_redirect` | Entrypoint that may redirect to canonical routes later. | Requires parameter compatibility proof before redirect. |
| `candidate_remove_later` | Handler that may be deleted later. | Requires tests proving no live navigation, form, assistant, external link, or export depends on it. |

## Canonical Owner Routes

These routes are owned by `admin_budget_routes.py`:

- `GET /admin/presupuestos`
- `GET /admin/presupuestos/export.xlsx`
- `GET /admin/presupuestos/torneo/{tournament_key}`
- `POST /admin/presupuestos/torneo/{tournament_key}/ingresos/import`
- `GET /admin/presupuestos/torneo/{tournament_key}/ingresos/export.xlsx`
- `GET /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/link`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/upload-link`
- `POST /admin/presupuestos/torneo/{tournament_key}/cfdi-ingresos/{link_id}/unlink`
- `POST /admin/presupuestos/versiones/create`
- `POST /admin/presupuestos/versiones/copy-forward`
- `POST /admin/presupuestos/versiones/{version_id}/lineas/create`
- `POST /admin/presupuestos/versiones/{version_id}/transition`
- `POST /admin/presupuestos/versiones/{version_id}/update`
- `POST /admin/presupuestos/lineas/{line_id}/update`

## Admin Routes Bridge And Legacy Inventory

These routes are the only approved Presupuestos namespace routes still allowed
inside `admin_routes.py` during C4:

| route | method | class | notes |
|---|---|---|---|
| `/admin/presupuestos-legacy` | GET | `legacy_reference` | Historical dashboard/reference only. Not a canonical owner. |
| `/admin/presupuestos/import-default` | POST | `candidate_remove_later` | Legacy default import action; not observed as a canonical UI target in C2b. Validate external/assistant dependencies before removal. |
| `/admin/presupuestos/conceptos/bulk-save` | POST | `bridge_required_by_canonical_ui` | Canonical UI bridge for concept updates. |
| `/admin/presupuestos/conceptos/{concept_id}/hide` | POST | `bridge_required_by_canonical_ui` | Canonical UI bridge for concept visibility. |
| `/admin/presupuestos/conceptos/export.xlsx` | GET | `bridge_required_by_canonical_ui` | Generated budget catalog export; not assistant artifact authority. |
| `/admin/presupuestos/conceptos/import` | POST | `bridge_required_by_canonical_ui` | Bridge import action; keep permissioned. |
| `/admin/presupuestos/versiones/{version_id}/lineas/import` | POST | `candidate_remove_later` | Legacy annual line import action; not observed as a canonical UI target in C2b. |

## Route-Target Evidence

C2b inspected current route targets without changing behavior.

Visible canonical entrypoints:

- `user_routes.render_top_navigation(...)` links authorized users to
  `/admin/presupuestos`.
- `admin_routes.py` admin advanced navigation links authorized users to
  `/admin/presupuestos`.
- `admin_routes.py` finance workspace quick actions link Presupuestos/Ingresos
  to `/admin/presupuestos`.
- `support_routes.py` includes a Presupuestos support entrypoint pointing to
  `/admin/presupuestos`.
- `operations_analytics_routes.py` exposes `presupuestos_url` as
  `/admin/presupuestos`.

Canonical UI bridge targets still used by `admin_budget_routes.py`:

- `/admin/presupuestos/conceptos/{concept_id}/hide`
- `/admin/presupuestos/conceptos/bulk-save`
- `/admin/presupuestos/conceptos/export.xlsx`
- `/admin/presupuestos/conceptos/import`
Version and line mutation bridge targets resolved during C4:

- `/admin/presupuestos/versiones/{version_id}/transition`
- `/admin/presupuestos/versiones/create`
- `/admin/presupuestos/versiones/{version_id}/update`
- `/admin/presupuestos/lineas/{line_id}/update`
- `/admin/presupuestos/versiones/{version_id}/lineas/create`

C4 moved these routes into `admin_budget_routes.py` while preserving public
paths, methods, permission checks, redirects, and service calls.

Legacy/candidate-remove targets not observed in the canonical UI source during
C2b:

- `/admin/presupuestos/import-default`
- `/admin/presupuestos/versiones/{version_id}/lineas/import`

External dependencies observed during C2c and resolved during C3:

- `/admin/presupuestos/export.xlsx` is referenced by
  `assistant_finance_read` as `budget_review_xlsx`.
- `/admin/presupuestos/export.xlsx` is referenced by the runtime artifact index
  as the Presupuestos review export.
- C3 moved `/admin/presupuestos/export.xlsx` into the canonical
  `admin_budget_routes.py` owner while preserving the public path and generated
  workbook contract for those dependencies.

Legacy visibility finding:

- No current navigation/form evidence should point users to
  `/admin/presupuestos-legacy`.
- `/admin/presupuestos-legacy` remains callable as `legacy_reference` only.
- Future C3 `hide` is therefore likely documentation/navigation-hardening first,
  not a behavior change, unless additional runtime evidence finds visible
  legacy entrypoints.

## Non-Authority Rules

- Bridge routes must not become owners for new AR, cashflow, planning, or
  assistant finance behavior.
- `/admin/presupuestos-legacy` must not be linked as the current Presupuestos
  entrypoint in navigation, assistant responses, export catalogs, or future
  finance roadmap docs.
- Assistant finance must cite canonical read models, not bridge handlers.
- Generated Presupuestos exports are report exports. They are not
  `assistant_artifacts` rows and are not a managed artifact archive.

## Future Cleanup Options

A later story/spec may choose one bounded action:

- `hide`: remove visible legacy navigation/form references while keeping routes
  callable.
- `redirect`: redirect legacy entrypoints only when parameter compatibility is
  proven.
- `remove later`: delete handlers only after tests prove no live navigation,
  form, assistant, external link, or export depends on them.
- `document only`: keep the current state when cleanup risk is higher than
  value.
