# SamChat accepted regression gate

This document is the release spine for fixes and UX improvements that have already been accepted by operations/finance and must not silently disappear in later branches, merges, or deploys.

The companion CI guard is `scripts/ci/check-accepted-regressions.py`. It is intentionally small and static: it checks for durable source markers that have historically been lost during hotfix branches or manual deploys. It does not replace functional tests; it prevents accepted behavior from becoming only conversational memory.

## Release rule

No PR should be merged or deployed if it removes one of these accepted behaviors without either:

1. replacing it with an equivalent behavior and updating the guard, or
2. documenting a deliberate product decision in this file.

Every production deploy must confirm:

- exact commit deployed;
- active `WorkingDirectory` for `samchat-gastos.service`;
- `/healthz` and `/readyz` green;
- this accepted regression gate green in CI or run locally.

## Accepted regression checks

| ID | Accepted behavior | Why it exists | Guard expectation |
| --- | --- | --- | --- |
| ARG-001 | Expense-report quick capture shows a CFDI PDF preview before saving. | Users must verify they attached the correct invoice/materiality before committing a gasto. | `quick_cfdi_pdf_preview` and `render_pdf_file_preview_script` remain wired in user expense routes. |
| ARG-002 | Food/restaurant/consumption expenses expose a tip field and include tip in the paid total. | AMEX meal charges often exceed the CFDI because tip is on the voucher; finance needs the tip as non-deductible. | `propina_no_deducible` remains wired through quick capture, edit flow, schema, and tests. |
| ARG-003 | CFDI XML/PDF does not overwrite the user-entered description/concept in quick capture. | The business description belongs to the user; CFDI description is evidence, not the operational concept. | Quick-capture autofill keeps invoice totals/folio/date but does not assign `descripcion_concepto_principal` into the user description field. |
| ARG-004 | Solicitudes de transferencia consultation includes provider search. | Finance/operations asked for search by provider in the consulta table, not inside the request form. | The `Por Proveedor` filter and related test remain present. |
| ARG-005 | Payment run moves closed-cut documents to `En Proceso de Pago` and blocks upstream rejection. | Once payment programming owns the item, the previous approver must not revive or reject it. | The payment-run page text and tests keep `En Proceso de Pago`. |
| ARG-006 | Expense reports keep Referencia Operaciones visible. | Operations tracks everything by its operational reference. | Expense report list/detail templates keep `Referencia Operaciones`. |
| ARG-007 | PDF/materiality preview and tip behavior are protected by focused tests. | UI regressions previously came from branches that reintroduced older templates. | Focused unit tests for quick expense totals and third-party request autofill remain present. |

## Backlog not yet guarded

These are known product commitments or requested improvements but are not yet stable enough to treat as accepted-regression gates:

- full parity of Informes de Gastos filters/layout with Solicitudes de Transferencia;
- air travel extras with multiple CFDI per AMEX charge;
- AMEX-only eligibility limited to Odilon, Luis Angel, and the Federicos;
- fully symmetric visual redesign of Solicitudes and Informes;
- budget-control routing before business approval for every applicable document;
- owner pack / assistant folders as a production UX surface.

Promote any item above into the guarded table after it is implemented, accepted, and has a source/test marker that CI can verify.
