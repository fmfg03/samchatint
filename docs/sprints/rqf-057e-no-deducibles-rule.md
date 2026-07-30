# RQF-057E - No deducibles accounting rule

Status: CLOSED_LOCAL_PENDING_COMMIT
Date: 2026-07-30
Plan master: customer Excel delta - ReglaNoDeducibles.

## Scope

Customer rule: when a Solicitud de Transferencia or an Informe de Gastos line is not linked to a factura/CFDI, accounting impact must use the project-specific Gastos No Deducibles account.

## Implementation

### Solicitudes de Transferencia

`register_document_payment()` now applies the No Deducibles project account when a paid `SOLICITUD` generates an `ExpenseReport` and the source document has no linked `cfdi_report_id`.

The implementation reuses the canonical project matrix in `expense_accounting_service._NO_DEDUCIBLE_ACCOUNT_BY_PROJECT` via `_resolve_project_no_deducible_account()`, avoiding a second mapping.

If the SOLICITUD already has a linked CFDI (`cfdi_report_id`), the helper does not run and does not overwrite the existing accounting classification.

### Informes de Gastos

The Informe line path already had project-specific No Deducibles support:

- `CuentaContableSuggester._apply_no_deducible_project_rule()` suggests the project-specific account when `has_cfdi=False`.
- `expense_accounting_service` resolves project-specific No Deducibles accounts for accounting previews/exports.
- Existing tests verify the project account matrix and that the suggester does not apply the rule when CFDI exists.

## Evidence

Focused tests:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/gastos/test_no_deducible_project_rules.py \
  tests/unit/gastos/test_solicitud_terceros_routes.py::test_quick_expense_cfdi_autofill_keeps_xml_total_as_authority -q
```

Result: `11 passed`.

Compile check:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m py_compile \
  src/devnous/gastos/services/documento_payment_service.py
```

## Guardrails

- No new accounting matrix was introduced.
- Existing project mapping remains the single source of truth.
- Linked CFDI documents keep their existing path; no forced No Deducibles override.
- No push/PR/merge performed in this phase.

## Non-claims

- This does not add a UI for editing the No Deducibles matrix.
- This does not automatically reclassify old historical expenses already generated before this cut.
- If a CFDI is linked after payment, existing generated expenses may still require review/reclassification depending on accounting workflow.
