# RQF-057G8 - Authorization warnings dashboard

## Objective

Give Finanzas/Admin a read-only dashboard for authorization-route soft warnings before any hard enforcement is enabled.

## Implemented

- Added `/admin/estrategias-autorizacion/warnings`.
- Reads `authorization_route_warning` metadata from `customer_success_audit_events` for `documento.approved` events.
- Shows document, requester, amount, required roles, matched roles, missing roles, and warning message.
- Supports filters by text query, missing role, and result limit.
- Added access-control tool key `configuracion.authorization_warnings` with read-only default for finance/admin roles.
- Added a panel card under Configuracion.

## Boundary

Read-only. It does not mutate documents, approvals, profiles, Telegram routing, or enforcement policy.

## Verification

- `python -m py_compile` for route/access-control files.
- Focused authorization/profile/document route tests.
