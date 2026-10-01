# ISH capture classification — local evidence

Date: 2026-10-01. Base: `main` `2465a4cbf115efa8d03b0ea11c685e35679f51ef`.
Branch: `fix/ish-budget-subtotal`. Evidence level: local implementation only.

## Approved rule and cause

Francisco requested ISH to contribute to Subtotal instead of Impuestos y
Retenciones for budget classification. This is a requested classification rule,
not a legal conclusion about SAT reporting.

`QuickExpenseTaxComponents.impuestos_y_retenciones` previously included every
local transfer. Thus a synthetic invoice with fiscal subtotal 1000, IVA 160,
ISH 36 and total 1196 prefills 1000 / 196 / 1196. The corrected capture prefills
1036 / 160 / 1196. The fiscal subtotal remains 1000 and IVA remains 160.

Only explicitly named local transfers (ISH, I.S.H., Impuesto sobre Hospedaje,
Impuesto sobre el Hospedaje, Impuesto al Hospedaje) move. Other local taxes,
unidentified labels and local withholdings retain their existing classification.
No inference from hotel names, expense text, location or rates is introduced.

## Owners, API, storage and presentation

- Target: `copa_telmex_dashboard.py` live-web runtime; gastos/informes capture
  and export, backed by the existing direct Postgres workflow.
- Read classification owner: `cfdi_autofill.py`; the original parser output and
  `QuickExpenseTaxComponents.subtotal` remain fiscal evidence.
- `/api/informes-de-gastos/cfdi-autofill` returns the adjusted capture fields
  through the existing autofill service. The existing form script consumes
  these same subtotal and tax fields without a second classification formula.
- `_quick_expense_values` uses the same capture subtotal for server validation.
  Shared invoices continue to accept the user's applied subtotal and taxes and
  prorate IVA by applied fiscal total; the correction does not add ISH twice.
- Canonical write path remains `create_expense_from_data` and CFDI ingestion /
  linking. It stores the unchanged expense total and IVA. No new ISH amount is
  written into `hospedaje_impuesto_monto`, and no CFDI field, schema, migration,
  historical record or production data is changed.
- `_informe_expense_export_amounts` includes identified ISH in the capture base
  and removes it from the tax column. Shared exports prorate the adjusted net
  base and use the remainder for taxes, preserving each row's total and tips.
- Budget actuals remain owned by `src/samchat/budgets/service.py` and canonical
  budget routes. Their existing expense total / IVA inputs are unchanged; no
  parallel budget persistence or query path was introduced.
- Accounting policy generation and fiscal/COI tax components are unchanged.

## Original references: visual blocker

Library references `libfile_a13cf7331c048191bfd3c4a9812cbe98` and
`libfile_1fdc51a39284819180ecc045ec8e9a78` were resolved with the current Library
skill and its resolved-reference materialization flow. The official helper
failed for both on this Windows executor with `AttributeError: module 'os' has
no attribute 'setxattr'`. No readable JPEG was installed at the local destination.
Neither capture was inspected; no visual claims are made. References and helper
inputs were kept outside the repository. Tests contain synthetic evidence.

## Validation

- Three canon Git-blob SHA-256 values match the convergence register.
- 11 new ISH regressions passed: labels, no ISH, IVA plus federal withholding,
  unchanged fiscal fields and total, other local taxes, and shared invoice
  rounding / allocation.
- Final focused pytest run: 25 passed, 5 deselected (11 new and 14 existing
  shared-invoice tests). The local runner caches identical large AST parses;
  each existing test still gets fresh production-function scopes and mocks.
- Six existing CFDI capture test bodies passed through an isolated source
  harness using the production route functions and XML parser, without booting
  the composite application.
- Ten existing SQL/Python budget and proration regressions passed.
- flake8: no diagnostics on added lines; complete autofill/new-test modules pass.
- Runtime packaging, registration operational surface and accepted-regression
  guards passed. Focused compile and diff checks passed.
- Full web application, integration suite, production and authenticated UI/UAT
  were not exercised. Five supplementary capture tests requiring the composite
  application are excluded from the focused shared-invoice run.

Canon unchanged: this implements the approved narrow capture classification
using existing domain owners and leaves canonical product, architecture,
authority, accounting grouping and release invariants unchanged. No additional
canonical decision is needed for local implementation. Publication and
production actions remain outside this request.
