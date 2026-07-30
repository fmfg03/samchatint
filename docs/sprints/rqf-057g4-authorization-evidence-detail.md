# RQF-057G4 - Authorization strategy evidence in document detail

## Objective

Surface the send-time authorization-strategy evidence on the document detail page without changing authorization routing or enforcement.

## Implemented

- Added an advisory renderer for the latest `documento.sent` audit event containing `authorization_strategy` metadata.
- Added a visible `Ruta de autorizacion sugerida` panel to `/documentos/{documento_id}` when evidence exists.
- Shows inferred inputs, suggested rule, required role keys, and matching authorization profiles.
- Keeps the panel explicitly advisory: it does not block, route, approve, reject, or replace the current workflow.

## Boundary

This closes visibility of evidence only. Enforcement of the matrix remains a later stage and must still pass preview/approval-path tests before it can affect production routing.

## Verification

- `python -m py_compile src/devnous/gastos/routes/user_routes.py`
- Focused authorization/document source tests.
