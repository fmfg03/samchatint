# RQF-057G5 - Authorization route preview

## Objective

Compare the advisory authorization matrix route against the approvals currently recorded on each document, without enforcing or changing the live workflow.

## Implemented

- Extended the document detail authorization panel with a `Comparacion consultiva` section.
- Loads actual document approval events from `aprobaciones` joined to `empleados`.
- Displays the registered route: date, action, approver, role, and department.
- Computes covered and missing suggested role keys using configured authorization profiles and employee matchers.
- Labels the state as pending, matching, or consultative difference.

## Boundary

This is still preview-only. It does not reroute notifications, change approvers, reject approvals, or block document transitions. The next safe enforcement stage should first add explicit preview acceptance tests for representative Plataforma Sports cases.

## Verification

- `python -m py_compile src/devnous/gastos/routes/user_routes.py`
- Focused authorization/profile/document route tests.
