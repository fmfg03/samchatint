# RQF-UX-002 — UAT baseline instrument

Status: READY_FOR_EXECUTION — no operator observation recorded
Date: 2026-09-29
Issue: #352

## Purpose

Capture a comparable before/after usability baseline without treating test
profiles, technical checks, or a successful deployment as business acceptance.

## Session rules

- Use a real authorized operator for the named role and a non-production
  training record where a task could write business state.
- Observe the task from `/panel`; do not give the route or module name.
- Do not record passwords, session cookies, RFCs, bank accounts, document
  content, beneficiary details, or personal data in the receipt.
- If a production record is opened, observe only; do not perform a financial
  mutation solely for this UAT.

## Tasks and observations

| Role | Prompt from `/panel` | Record |
| --- | --- | --- |
| Employee | “Inicia una solicitud a tercero y ubica dónde adjuntar su comprobante.” | first click, selected route, completion/blocker, confusing label |
| Approver | “Encuentra una solicitud que requiere tu decisión.” | first click, context sufficiency, next owner after decision preview |
| Control Presupuestal | “Ubica un documento detenido por asignación.” | first click, blocker/concept context, valid options visible |
| Finance | “Prepara el siguiente corte y revisa comprobantes pendientes.” | first click, distinction cutoff/proof/paid, wrong turns |
| Accounting | “Revisa una clasificación COI y una excepción de conciliación.” | first click, evidence/audit clarity, route terminology |
| Direction | “Identifica qué requiere atención en tu portafolio.” | first click, scope clarity, drill-down result |

## Receipt template

Record one row per observation:

| Role | Task | First click label | Route reached | Outcome | Confusion/blocker | Time optional | Follow-up |
| --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  | completed / blocked / abandoned |  |  |  |

## Evidence boundary

This instrument becomes UAT evidence only after the owning operator, date,
scope, and outcome are recorded. A completed automated navigation contract is
technical evidence only.
