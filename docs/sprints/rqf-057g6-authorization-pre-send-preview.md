# RQF-057G6 - Authorization pre-send preview

## Objective

Show the advisory authorization matrix result before a draft document is sent, without changing or blocking the live send workflow.

## Implemented

- Added a `Preview de autorizacion al enviar` section on document detail for drafts that the current owner can send.
- Uses the same `build_document_authorization_evidence` path that persists send-time evidence.
- Explicitly loads the document owner before inference so the preview does not depend on async lazy loading.
- Shows suggested rule, area, erogation type, amount, required roles, candidate profiles, and fallback reason when present.

## Boundary

Preview-only. It does not reroute Telegram, change assigned approvers, block submit, or require user confirmation. Enforcement remains pending until representative matrix cases are accepted.

## Verification

- `python -m py_compile src/devnous/gastos/routes/user_routes.py`
- Focused authorization/profile/document route tests.
