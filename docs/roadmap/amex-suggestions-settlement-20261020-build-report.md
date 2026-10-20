# AMEX: informe de implementación y verificación

Fecha: 2026-10-20. Estado: implementación aislada y verificación técnica completadas; validación independiente CLEAN_WITH_NOTES, sin hallazgos pendientes. Base: `0df07ace2b13e942c322b30f88a4c5b811b85e62`. Historia y especificación aprobadas: `amex-suggestions-settlement-20261020-story.md` y `amex-suggestions-settlement-20261020-spec.md`.

## Resultado funcional

El estado de cuenta presenta candidatas CFDI ordenadas por identidad, importe y descripción; las fechas bancaria y fiscal permanecen separadas, incluso entre meses/años. La revisión muestra diferencias y PUE/PPD; sólo una confirmación explícita vincula. La búsqueda manual sigue disponible. El ranking SQL considera el catálogo fiscal autorizado antes del límite; la evidencia XML inválida no equivale a búsqueda completa vacía. La navegación comparte un límite de 1600 bytes y cuatro tokens por espacio en la sesión existente; la prueba real de cookie firmada conserva autenticación, CSRF y acceso al cargo 26.

La política solicitada para todas las tarjetas de Plataforma usa el RFC receptor configurado PSP1705058S4. REP local es evidencia opcional; no bloquea IVA ni póliza. El modo directo exige importes fiscales demostrados por XML; no calcula IVA estimado ni propina por diferencia. Las llamadas anteriores mantienen sus defaults. El snapshot congela evidencia y versión.

R1 crea gasto/impuestos/pasivo y presupuesto una sola vez. R2 prueba el reconocimiento previo y conserva sus pólizas; un estado sólo R2 genera recibo de finalización sin póliza vacía. R3 cancela el crédito real de una cuenta de gastos histórica, cargando su cuenta original y abonando la tarjeta, sin duplicar gasto, IVA ni presupuesto. La selección explícita no contabiliza; la confirmación vuelve a verificar ámbito, versión, saldo y origen. Tanto el crédito R3 como el pasivo R2 histórico validan el XML canónico de su reconocimiento original, incluido origen user_upload, sin exigir una nueva descarga SAT ni relajar el origen SAT de R1. El pago de tarjeta conserva su pantalla y owner existentes.

## Archivos

Archivos de este candidato (incluye fuentes nuevas, pruebas y evidencia):

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261020_amex_expense_account_settlements.sql`
- `database/migrations/20261020_amex_expense_account_settlements_rollback.sql`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-settlement-backend-build-20261020.md`
- `docs/roadmap/amex-settlement-lockgraph-20261020.md`
- `docs/roadmap/amex-suggestions-settlement-20261020-acceptance.md`
- `docs/roadmap/amex-suggestions-settlement-20261020-build-report.md`
- `docs/roadmap/amex-suggestions-settlement-20261020-coverage.json`
- `docs/roadmap/amex-suggestions-settlement-20261020-spec.md`
- `docs/roadmap/amex-suggestions-settlement-20261020-story.md`
- `docs/roadmap/amex-suggestions-settlement-20261020-validation.md`
- `docs/roadmap/samchat-convergence-register.md`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/amex_settlement_models.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_direct_routes.py`
- `src/devnous/gastos/services/amex_accounting_posting_service.py`
- `src/devnous/gastos/services/amex_direct_accounting_service.py`
- `src/devnous/gastos/services/amex_direct_cfdi_search_service.py`
- `src/devnous/gastos/services/amex_direct_common.py`
- `src/devnous/gastos/services/amex_direct_context_service.py`
- `src/devnous/gastos/services/amex_direct_fiscal_evidence_service.py`
- `src/devnous/gastos/services/amex_direct_navigation.py`
- `src/devnous/gastos/services/amex_direct_schema_contract.py`
- `src/devnous/gastos/services/amex_direct_schema_service.py`
- `src/devnous/gastos/services/amex_direct_suggestion_service.py`
- `src/devnous/gastos/services/amex_expense_account_settlement_service.py`
- `src/devnous/gastos/services/amex_recognition_service.py`
- `src/devnous/gastos/services/employee_debtor_accounting_service.py`
- `src/devnous/gastos/services/expense_accounting_service.py`
- `tests/acceptance/test_amex_sat_budget_direct_postgres.py`
- `tests/acceptance/test_amex_suggestions_settlement_journey.py`
- `tests/integration/test_amex_direct_backend_postgres.py`
- `tests/integration/test_amex_direct_schema_postgres.py`
- `tests/integration/test_amex_direct_suggestions_postgres.py`
- `tests/integration/test_amex_settlement_postgres.py`
- `tests/unit/gastos/test_amex_direct_cfdi_search.py`
- `tests/unit/gastos/test_amex_direct_fiscal_evidence.py`
- `tests/unit/gastos/test_amex_direct_navigation_cursors.py`
- `tests/unit/gastos/test_amex_direct_routes.py`
- `tests/unit/gastos/test_amex_direct_schema_admission.py`
- `tests/unit/gastos/test_amex_direct_suggestions.py`
- `tests/unit/gastos/test_amex_settlement_historical_identity.py`
- `tests/unit/gastos/test_amex_suggestions_settlement_routes.py`

## Pruebas y comandos

**909 pruebas unitarias aprobadas**, 38 advertencias heredadas, 34.39 s. Log: `/tmp/amex-suggestions-unit-final-20261020.log`.

```sh
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps COVERAGE_FILE=/tmp/amex-suggestions-units-final-20261020.coverage /root/samchat/.venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest -q --no-cov tests/unit/gastos/test_amex*.py tests/unit/gastos/test_finance_budget_tax_regressions.py tests/unit/test_expense_accounting_discount_fallback.py
```

**99 pruebas PostgreSQL aprobadas**, una omisión explícita del regenerador de metadata (herramienta de mantenimiento, no escenario de negocio), 3219 advertencias heredadas, 238.85 s. Incluye 27 casos de liquidación, 40 de integridad/esquema, 27 regresiones del backend directo y cinco del ranking SQL real. Log: `/tmp/amex-suggestions-pg-final-20261020.log`.

```sh
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test COVERAGE_FILE=/tmp/amex-suggestions-pg-final-20261020.coverage /root/samchat/.venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest -q --no-cov tests/integration/test_amex_settlement_postgres.py tests/integration/test_amex_direct_schema_postgres.py tests/integration/test_amex_direct_backend_postgres.py tests/integration/test_amex_direct_suggestions_postgres.py
```

Los casos físicos incluyen: R1/R2/R3 juntos en un corte; sólo R2 sin póliza vacía; R2 y R3 con XML original user_upload; XML faltante/malformado/contradictorio; dos estados compitiendo por el mismo crédito; reintento idempotente; rollback tras fallo tardío incluso si el caller captura la excepción; mutación del origen en ambos órdenes de carrera; revocación de ámbito; 27 orígenes paginados; tokens ajenos/falsos/obsoletos; escrituras ajenas sin bloqueo de liquidación; replay/rollback de esquema y preservación de controles históricos.

**Cuatro recorridos de aceptación aprobados**, cero omisiones, 151 advertencias heredadas, 29.00 s. Dos recorren navegador real con Chromium (R1 de Suntory $3814, cargo 29/septiembre y CFDI 6/octubre PPD sin REP; R3 con XML original user_upload y crédito real de comprobación), dos ejecutan el owner físico de pago de tarjeta con/sin corte. Log: `/tmp/amex-suggestions-browser-acceptance-20261020-final-covered.log`. Reporte independiente: `amex-suggestions-settlement-20261020-acceptance.md`.

Los recorridos verifican preview realmente READ ONLY, ausencia de consultas SAT en R3, confirmación explícita, freeze, replay idempotente, partidas y sumas físicas, RFC sin espacios adicionales y UUID original en cada formato CSV/XLSX/ZIP. El R1 usa los importes del XML y conserva evidencia PPD/no-REP/política operativa; R3 conserva exactamente dos movimientos sin nuevos IVA/presupuesto. El pago sólo afecta pasivo/banco.

```sh
PYTHONPATH=src:.:tests/acceptance:/tmp/samchat-amex-source-testdeps TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test COVERAGE_FILE=/tmp/amex-suggestions-acceptance-final-20261020.coverage /root/samchat/.venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest --no-cov tests/acceptance/test_amex_suggestions_settlement_journey.py tests/acceptance/test_amex_sat_budget_direct_postgres.py::test_payment_owner_schedules_and_posts_only_liability -q --tb=short
```

**Cobertura de sentencias ejecutables cambiadas: 780/863, 90.38 %** (umbral 85 %). Combina únicamente las tres ejecuciones finales sobre las mismas huellas de fuente, incluye archivos nuevos sin trackear y compara contra la base indicada. No confunde cobertura de líneas Python con verificación de SQL/concurrencia o aceptación fiscal real. Recibo durable con archivos/huellas y líneas faltantes: `amex-suggestions-settlement-20261020-coverage.json`; cálculo: `/tmp/amex-suggestions-changed-coverage-20261020.py`.

Total: **1012 pruebas aprobadas**; una omisión de mantenimiento expresamente identificada. No hay pruebas fallidas pendientes atribuibles al cambio. Las pruebas usan sólo fixtures sintéticos y PostgreSQL desechable en 127.0.0.1:55471, nunca producción ni SAT real.

## Controles y fallos conocidos

Los errores detectados durante la construcción se corrigieron conservando las verificaciones: restricción indebida de origen SAT sobre XML histórico R2/R3, asociación legacy que debía exigir selección explícita, UUID fiscal original ausente en exportación R3, retención sintética incompleta a nivel concepto, catálogo de controles que omitía nombres históricos, cursor que podía saturar cookie, orden de rollback de fixtures y errores de formato/typing. Las pruebas históricas de navegación se mantienen intactas; las nuevas pruebas de cursor son aditivas.

El lint focal de diez módulos informa 190 diagnósticos sobre líneas preexistentes y cero sobre líneas modificadas. Mypy focal sobre diez módulos informa diez errores de rutas preexistentes, contrastados con los mismos diez del baseline (dos ramas inalcanzables y ocho argumentos del owner de recuperación). No se declara que el lint o typecheck global sean verdes. Los nuevos módulos y owners corregidos tienen comprobaciones focales limpias; evidencia final y comandos abajo.

## Comandos de calidad y atribución

- `git diff --check`: limpio.
- Inventario estático: `PYTHONPATH=src:. /root/samchat/.venv/bin/python scripts/private_plugin/inventory.py`: 606 rutas, 574 montajes estáticos vivos, 44 acciones canónicas y cero errores de parseo; JSON/CSV actualizados.
- `black --check` sobre rutas directas, cuatro servicios nuevos y cuatro archivos de pruebas modificados/nuevos: nueve archivos sin cambios requeridos.
- `mypy --follow-imports=silent` sobre rutas, common/context y siete owners/modelos focales: diez errores heredados en rutas, cero errores nuevos. Comparación normalizada con `/tmp/amex-root-mypy-baseline-20261020.log`: idénticos diagnósticos. Log focal: `/tmp/amex-suggestions-final-mypy-20261020.log`.
- `flake8 --max-line-length=88 --extend-ignore=E203` sobre los diez módulos focales: 190 diagnósticos en líneas heredadas, cero en líneas cambiadas. Log: `/tmp/amex-suggestions-final-lint-20261020.log`. Los owners nuevos tienen además controles focales Black/isort/flake8/mypy limpios.
- Integridad del canon: los tres SHA-256 coinciden con el registro. Recibo: `/tmp/amex-suggestions-canon-final-20261020.json`.
- Versión medida: `/tmp/amex-suggestions-verification-source-freeze-20261020.json` conserva veinte huellas de fuente y SQL; comprobadas sin deriva después de las pruebas.

## Canon, despliegue y límites

Se incorporaron exclusivamente los textos exactos de producto/ingeniería aprobados en §10 de la especificación y se actualizaron los hashes del registro. Sweep unchanged: inventario histórico sin observación productiva nueva. Ninguna regla canónica adicional se autorizó implícitamente.

La migración de liquidaciones es necesaria antes de activar este código. Se verifica replay, rollback vacío y rechazo de rollback con evidencia en PostgreSQL desechable; su ejecución productiva requiere autorización separada. La admisión del esquema conserva todos los guardas históricos y añade los nuevos.

No hay commit, push, PR, merge, migración productiva, deploy, vínculo Suntory real ni aceptación de Contabilidad. La simulación demuestra comportamiento con datos sintéticos, no correspondencia fiscal de la factura real. Los CFDI de origen ya asignados quedan congelados por el guarda SQL; cambios posteriores deben conservar el historial y pasar por el owner autorizado. No se modificaron dependencias ni se mezcló el trabajo multiempresa.

Desviaciones de alcance: ninguna; los ajustes de paginación, controles, concurrencia y exportación cierran criterios ya aprobados. La revisión independiente final, CLEAN_WITH_NOTES y sin hallazgos Critical/Important/Minor pendientes, se documenta en `amex-suggestions-settlement-20261020-validation.md`. Las notas conservan deuda heredada y límites de UAT/operación/publicación; no otorgan autoridad de despliegue.

El PostgreSQL desechable de este trabajo se detuvo al liberar todas las pruebas; no se tocó el clúster paralelo de multiempresa ni producción.

Diff completo revisable (incluye archivos nuevos): `/tmp/samchat-amex-suggestions-settlement-final-20261020.diff`.

## Cierre de publicación autorizado

El usuario autorizó commit, push, PR, merge, migración y deployment el 2026-10-20. El chequeo sobre el índice incorporando archivos nuevos encontró espacios finales en 104 líneas DDL de la migración. Se eliminaron sólo esos espacios; cuerpos de funciones y Python idénticos. Huella final SQL: `48a3b80c` (prefijo). Validador independiente confirmó continuidad de alcance/aprobaciones y CLEAN_WITH_NOTES. Revalidación exacta del esquema con esos bytes: **40 PASS**, 46.54 s, `/tmp/amex-deploy-whitespace-schema-20261020.log`. Chequeo completo del índice limpio; gates de registro, regresiones aceptadas y packaging aprobados. La evidencia anterior de 1012 pruebas corresponde al candidato funcionalmente idéntico antes de este ajuste de espacios.

Primer CI de publicación: el inventario estático reproducible detectó source_file_count468 frente a473 por cinco servicios nuevos incorporados a Git después de su generación inicial. Se regeneró el inventario: sólo ese contador cambia, las 606 rutas permanecen iguales. Sus nueve tests focales reales aprobaron (20.858 s), `/tmp/amex-pr493-inventory-regression.log`. Revisión independiente confirmó metadata dentro del alcance y aprobaciones vigentes; sin cambio de runtime. El fallo inicial no se trata como deuda heredada.
