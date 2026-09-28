# No Deducibles control — governance decision

Date: 2026-09-25

## Operational rule

For the monthly control, each `Solicitud` or `Informe` expense line without a
linked canonical fiscal CFDI is classified as `No deducible`. The CFDI link may
belong to the expense itself or to its source `Solicitud`/`Informe`. The control
period is the calendar month of the expense date, not the accounting month of
the policy. The projection retains the source document, tournament, phase,
expense reference, employee, currency, linked-CFDI state, and resulting
reason. Amounts remain separated by currency; the control does not convert or
combine them.

This is an evidence-presence control. It does not, by itself, establish a SAT
legal conclusion beyond the absence of a linked canonical CFDI record.

## Canon unchanged

Canon unchanged. This PR adds a read-only finance projection, UI, and XLSX
export derived from existing `ExpenseReport` and linked `CFDIReport` data. It
does not introduce persistence, a new authority route, a workflow-state
transition, a schema change, a release/deployment claim, or Finance UAT or
business-acceptance evidence. The operational rule above is user-approved but
does not amend the protected product or engineering canons. Any amendment to
those canons requires explicit human review and must not be authored by
automation.

## Evidence level

The feature is repository work under review until its pull request is merged.
It is not evidence of deployment, production reconciliation, Finance UAT, or
business acceptance.


## Date-range enhancement

The control also accepts an inclusive range of expense dates (Desde and Hasta) together with an optional tournament. The former month selector is a convenient default only. A tournament label such as Liga or Copa does not infer or create a season calendar: the selected dates remain the auditable control period. This remains a read-only query and export over the same canonical expense and CFDI links; it introduces no authority, persistence, workflow, or schema change. Canon unchanged for the same reason.
