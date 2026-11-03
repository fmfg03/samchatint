# ANTICIPO-PROVEEDORES-PRECISION-001

Fecha: 2026-11-03. Historia y especificación aprobadas por el usuario.
Implementación y validación autorizadas. Efectos productivos pendientes de autorización.

## Historia y aceptación

El solicitante debe poder comprobar un anticipo a proveedor pagado por su saldo
a centavos sin errores por residuos de precisión. Las aplicaciones totales y
parciales deben funcionar, conservando el bloqueo de excesos reales y todas las
protecciones de identidad, pago, reservas e inmutabilidad.

## Evidencia y propietarios

Runtime: `copa_telmex_dashboard.py`, servicio `samchat-gastos.service`.
Dominio: anticipos de proveedores, PostgreSQL directo. Escritura canónica:
`submit_supplier_invoice` en `supplier_advance_service.py`, con transacción
del llamador y guard SQL `guard_supplier_advance_document`.

La investigación verificó el release `cb767d47a397eae7731e1c8277f4d122008f0f0a`.
S-26000449 guarda `36429.169999999998253770172595977783203125`; Python aplica
`36429.17`. Un SELECT READ ONLY reprodujo la comparación que causa el rechazo.
No existían comprobaciones vinculadas guardadas en ese corte. Siete de nueve
anticipos pagados presentaban el mismo riesgo, no siete fallos confirmados.

## Implementación propuesta

- Añadir `database/migrations/20261103_supplier_advance_cent_precision.sql`,
  transaccional y repetible, que reemplace la función del guard existente.
- Cambiar únicamente la comparación del límite a
  `reserved + NEW.supplier_advance_applied > round(parent.monto_solicitado, 2)`.
  PostgreSQL round(numeric, 2) coincide con Decimal/ROUND_HALF_UP para los
  importes positivos del flujo. No introducir tolerancia adicional.
- Conservar bloqueo FOR UPDATE del padre, exclusión del hijo actual,
  liberación por rechazo/cancelación y las demás protecciones del guard.
- Conservar intacta la migración histórica `20261008_supplier_advances.sql`;
  las instalaciones nuevas deben aplicar después la nueva migración.
- Reutilizar `money()` y `advance_balances()` existentes. Sin cambios previstos
  en servicio, modelos, permisos, API, interfaz o trabajos de fondo.
- Sin UPDATE de documentos históricos, backfill, cambio global de tipos,
  nuevas dependencias ni cambios de reglas contables.

Trabajar en rama/worktree aislada desde la base integrada vigente, contrastada
con el release servido. El checkout principal contiene trabajo ajeno y su HEAD
no incluye estos archivos; no mezclar ni alterar ese trabajo.

## Archivos previstos y verificación

- Migración nueva indicada arriba.
- `tests/integration/supplier_advance_migration.mjs`.
- `tests/unit/gastos/test_supplier_advances.py`.
- Esta especificación y `docs/roadmap/supplier-advances.md` para alcance,
  orden de migraciones y evidencia de cierre.

Reproducir primero el rechazo con el importe exacto y el guard anterior en
PostgreSQL aislado. Aplicar la migración y verificar aplicación total de
36429.17, residuos subcentavo por ambos lados, aplicaciones parciales acumuladas
y rechazo de exceso real de 0.01. Comprobar repetibilidad de la migración y
preservar pruebas de identidad, pago confirmado, reservas, inmutabilidad,
rechazo/cancelación, no reapertura y unicidad contable.

Ejecutar pytest del archivo de anticipos, el script SQL con PGlite existente o
PostgreSQL desechable y los controles pertinentes de formato/lint. Verificar
saldo y asignación Python con los mismos importes. SQLite no demuestra el
comportamiento de los triggers ni el bloqueo PostgreSQL. No usar producción
para pruebas de escritura. Validar después en solo lectura contra historia,
especificación, reglas y resultados; atribuir cualquier fallo o check pendiente.

## Riesgos, activación y reversión

La corrección requiere migración de lógica SQL; cambiar solo Python no basta.
La activación productiva queda pendiente de autorización de efectos: preflight
de función y esquema reales, respaldo validado, copia de la función previa,
aplicación por el propietario y lectura posterior. No ejecutar DDL al arrancar.
El solicitante autorizado realizará la comprobación real por el flujo normal;
las pruebas técnicas no constituyen UAT ni aceptación financiera.

Reversión: restaurar transaccionalmente la función previa conservada, sin borrar
comprobaciones ni modificar documentos. Reintroduce el defecto de precisión y
no revierte comprobaciones legítimas; requiere verificación posterior.

## Canon

Canon unchanged: se alinea el límite SQL con los centavos que ya usa el servicio;
se conservan arquitectura, autoridad, inmutabilidad y límite de saldo. Los tres
hashes protegidos fueron comprobados. No se propone editar el canon.


## Implementation evidence — 2026-11-03

Base: `origin/main` after fetch, `12aacc0d3948f88a8ae176cbd2bcd1c9a00afe6f`.
Branch: `fix/supplier-advance-cent-precision-20261103`.
Worktree: `/root/samchat/.worktrees/supplier-advance-cent-precision-20261103`.
The relevant original migration, service and test files matched the serving
release inspected during investigation. Unrelated work in the root checkout
was preserved. Only the five files listed in this specification were changed.

### Commands and results

Run from the isolated worktree:

```sh
node tests/integration/supplier_advance_migration.mjs /tmp/supplier-advance-native-pg-adapter.mjs
PYTHONPATH=src:.:/tmp/supplier-advance-test-deps-20261103 /root/samchat/.venv/bin/python -m pytest tests/unit/gastos/test_supplier_advances.py -q --no-cov --tb=short
/root/samchat/.venv/bin/python -m black --check tests/unit/gastos/test_supplier_advances.py
/root/samchat/.venv/bin/python -m isort --check-only tests/unit/gastos/test_supplier_advances.py
/usr/bin/flake8 --max-line-length=88 tests/unit/gastos/test_supplier_advances.py
node --check tests/integration/supplier_advance_migration.mjs
git diff --check
```

- Red before fix: the new SQL regression failed with
  `Supplier advance allocation exceeds paid balance` on the exact incident
  amount under the original function.
- Green after fix: full SQL suite passed against disposable PostgreSQL 16;
  migration rerun, existing guards, total/partial cent allocations and real-cent
  overallocations were verified. The test retains the old-function rejection
  assertion before applying the new function, so both behaviors remain checked.
- Python: 43 passed, including 4 new precision cases; 867 deprecation warnings
  from existing SQLAlchemy/datetime code. No production reads/writes in tests.
- Black, node syntax and diff hygiene: passed.
- isort: full file fails on existing local import ordering. The same check
  failed on `git show HEAD:tests/unit/gastos/test_supplier_advances.py` with
  the worktree settings; imports were not changed by this correction.
- flake8: 7 existing errors at the repo limit of 88 columns (4 E501, 3 E741);
  no new errors. Baseline also had an unused datetime import, now used by the
  new precision tests. Existing style debt was not expanded into this fix.
- Initial pytest attempt: 21 passed / 22 setup errors due to missing aiosqlite
  in the project venv (environment issue). Resolved without installation by
  linking the existing package from `/root/.venvs/factory-adk-2.5.0` into the
  temporary test-only path shown above. Final complete file passes.
- The native adapter in `/tmp/supplier-advance-native-pg-adapter.mjs` starts its
  own PostgreSQL cluster with a private temporary socket and no TCP listener,
  and stops/removes that cluster. It is a local validation artifact, not a
  project or production dependency. PGlite itself was not installed.

Acceptance: all functional criteria in the approved story are covered and pass.
Quality validation is partial because full-file isort/flake8 have the pre-existing
failures above. Repository-wide CI, concurrent multi-session stress testing,
production activation and authenticated Finance UAT are not claimed. No Python
runtime change requires a new application build or typecheck for this SQL-only
behavior correction. The SQL function's changed condition is exercised on both
its accepting and rejecting branches.

No deviations from the approved implementation scope. Native PostgreSQL is an
explicitly allowed alternative to existing PGlite. Before any future activation,
reconcile the actual serving function against this migration and retain the
separate migration, release and business-acceptance authority boundaries.


## Publication and activation authority — 2026-11-03

After reviewing the complete diff and functional evidence, the user authorized
“aptobado hasta deploy”. This authorizes commit, push, PR, merge, controlled
owner-run migration and deployment for this exact correction. It does not
submit an invoice or replace authenticated Finance UAT.

Pre-publication gates passed: runtime packaging consistency, registration
operational surface, accepted regressions, PR quality-gate contract, JavaScript
syntax and diff hygiene. Final read-only review found no new blocking issues;
the inherited lint/import-order caveats remain recorded above.
