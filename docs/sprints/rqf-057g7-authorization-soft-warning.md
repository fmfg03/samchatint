# RQF-057G7 - Authorization soft warning

## Objective

Persist and surface a non-blocking warning when the actual approval route does not cover all advisory authorization-matrix roles.

## Implemented

- Added `build_authorization_route_soft_warning` to compare required role keys against actual approvals.
- On `documento.approved`, the workflow records `authorization_route_warning` in audit metadata when roles are missing.
- Warning calculation is best-effort; failures are logged and never block approval.
- Document detail now renders `Warning suave de autorizacion` when such an audit warning exists.

## Boundary

Still no hard enforcement. The system permits the approval and records the discrepancy for audit/review. Telegram routing, assigned approvers, and document state transitions remain unchanged.

## Verification

- `python -m py_compile` for touched service and route files.
- Focused authorization/profile/document route tests.
