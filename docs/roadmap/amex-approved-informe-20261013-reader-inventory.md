# AMEX: inventario acotado de lectores — 2026-10-13

Base verificada: 3619ebcab586d96d6740c5dda2a578dd74bd83aa. Parte de la especificación aprobada; no introduce reglas fiscales nuevas.

- `documento_service.py::validate_shared_cfdi_payment_amount`: suma reservas de ExpenseReport. Excluir exclusivamente ancla bancaria statement/report_bridge ya declarada; mantener los report targets como reservas reales.
- `cfdi_ingestion_service.py::_other_expense_links` y `find_blocking_cfdi_usage`, `cfdi_expense_link_service.py::_enforce_expense_cfdi_uniqueness`: no contar el header de control como otra aplicación. Sin bridge explícito los conflictos siguen vigentes.
- `expense_coi_export_service.py::expense_coi_batch_period_condition` y `build_expense_cfdi_for_export`: impedir exportación independiente de headers bridge. No recalcular cortes congelados.
- `samchat/budgets/service.py::_build_budget_scope_filters` y agregados dependientes, shared-CFDI sibling probes, y `samchat/budgets/executive_facts.py::build_executive_facts`: excluir headers de gasto real/conteos y de sondas de duplicado. Detalle por solicitud comparte el mismo filtro de identidad.
- `expense_accounting_service.py` y `amex_accounting_posting_service.py` están a cargo del builder contable; conservar fiscalidad, repartos confirmados y snapshots de cut.

Predicado común: EXISTS sobre consumo statement con binding_mode=report_bridge y imported_expense_id igual al ExpenseReport real. Ese modo sólo nace por selección explícita y es permanentemente no posteable, incluso sin hijos. Ninguna comparación por CFDI/importe/fecha implica identidad. Schema nuevo se provisiona exclusivamente por migración; no fallback ni DDL en lectores.
