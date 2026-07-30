# RQF-057C — Regional operator beneficiaries

Status: CLOSED_LOCAL_PENDING_COMMIT
Date: 2026-07-30
Plan master: customer Excel delta — Anticipos e Informes de Gastos for Operadores Regionales.

## Scope

Operations users who are already authorized to request for third-party employees can now request Anticipos and create Informes de Gastos for active Regional Operators.

This is intentionally not an authorization-router rewrite. The requester remains the authenticated employee and the approval route remains attached to the requester. The Regional Operator is only the beneficiary/payee class for the document/informe.

## Implementation

- Added `CuentaDeGastos.beneficiario_proveedor_cliente_id` to persist an external/regional-operator beneficiary for expense reports.
- Added schema guard migration for `cuentas_de_gastos.beneficiario_proveedor_cliente_id` and index.
- Added active Regional Operator lookup using `ProveedorCliente.tipo == "operadores_regionales"`.
- Added optional Regional Operator selector to:
  - `/gastos-terceros/solicitar-anticipo`
  - `/informes-de-gastos/crear`
- For Anticipos, selecting a Regional Operator swaps the bank-account selector to the operator account.
- For Informes, the new cuenta and INFORME document persist the operator as provider-client beneficiary while keeping the requester owner.
- Kept `ProveedorCliente.documentos` semantically bound to `Documento.proveedor_cliente_id` only, so employee/operator-beneficiary fields do not pollute provider semantics.

## Guardrails preserved

- Unauthorized users do not get the Regional Operator selector.
- Existing employee-beneficiary flow remains the default.
- Existing informes without a selected operator continue as employee/self-beneficiary.
- Approval ownership is not changed by beneficiary choice.
- Operator selection requires an active `operadores_regionales` record.

## Evidence

Focused tests:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest   tests/unit/gastos/test_solicitar_anticipo_route.py   tests/unit/gastos/test_informe_beneficiary_selector.py -q
```

Result: `20 passed`.

Compile check:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m py_compile   src/devnous/gastos/models.py   src/devnous/gastos/schema_guard.py   src/devnous/gastos/routes/user_routes.py
```

## Non-claims

- No change to hard enforcement of authorization matrix.
- No claim that every Regional Operator has valid bank/fiscal data; records must exist in `proveedores_clientes` as active `operadores_regionales`.
- No push/PR/merge performed in this phase.
