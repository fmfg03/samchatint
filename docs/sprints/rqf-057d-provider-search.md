# RQF-057D - Solicitudes search by proveedor

Status: CLOSED_VERIFIED_LOCAL
Date: 2026-07-30
Plan master: customer Excel delta - Buscador tab.

## Scope

Customer request: in the Solicitudes de Transferencia view by Referencia, add a provider search box alongside the existing Concepto and Referencia filters.

## Finding

The functionality existed in `/gastos-terceros`, but the UI placement was not explicit enough for the customer screenshot. This cut makes the query filters visible as a dedicated bar above the table:

- `terceros-search-ref` filters by Referencia Operaciones.
- `terceros-search-proveedor` is labeled `Por Proveedor`.
- `terceros-search-concepto` filters by Concepto.
- Each row includes `data-proveedor` for client-side filtering.
- The filter normalizes accents and punctuation before comparison, so provider searches are forgiving.

## Evidence

Existing focused test:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/gastos/test_solicitud_terceros_routes.py::test_gastos_terceros_includes_provider_search_filter -q
```

Result: `1 passed`.

UI correction commit keeps this as a consultation/list filter, not a field inside the Solicitud capture form.

## Files inspected

- `src/devnous/gastos/routes/user_routes.py`
- `tests/unit/gastos/test_solicitud_terceros_routes.py`

## Non-claims

- No backend/server-side provider filtering was added in this cut; current implementation is client-side over the visible scoped result set.
- No UI redesign was performed.
- No push/PR/merge performed in this phase.
