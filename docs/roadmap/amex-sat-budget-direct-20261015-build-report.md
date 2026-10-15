# AMEX directo SAT → presupuesto → póliza: reporte de construcción

Fecha: 2026-10-15. Estado: implementación y verificación técnica terminadas; revisión independiente sin hallazgos abiertos. Aprobación humana de commit/push/PR/merge recibida; PR #488 en validación remota. Migración y despliegue no autorizados por esta aprobación.

## Autorización y alcance

Historia y especificación aprobadas por el usuario, junto con el texto exacto de las enmiendas canon. Objetos versionados: `amex-sat-budget-direct-20261015-story.md` y `amex-sat-budget-direct-20261015-spec.md`. Base `d8c9fda23ed1b1bb1a68a08bc524fedbdb6d4e5f`, rama `feat/amex-sat-budget-direct-20261015`, worktree aislado. Checkout primario preservado. Esta fase no incluye commit, push, PR, merge, migración productiva, despliegue ni reparación histórica.

## Resultado implementado

- CSV original privado íntegro, tarjeta y período declarados; preview de signos/perfil, cargos, créditos y pendientes conservados.
- Matching contra CFDI SAT ya descargados, identidad exacta y unicidad global; vigencia observada con consentimiento, límites y continuación. No informes ficticios, nuevos downloads SAT, cron ni credenciales.
- Contabilidad selecciona proyecto/torneo y categoría; Control Presupuestal asigna sólo la partida con identidad/ámbito reales. Las cuentas se resuelven de mappings activos, con pasivo de la tarjeta.
- Corte íntegro, idempotente e inmutable, como máximo una póliza nueva por estado; snapshots para exportación COI compartida. La programación y el pago permanecen en el owner existente.
- Presupuesto proyecta base fiscal evidenciada, muestra faltantes sin inventar cero y evita sumar proyección más ledger. Revisiones conservan assignments y eventos históricos, usando únicamente el pointer actual.
- Nueve tablas owner-run y SQL/rollback preparados; runtime excluye esas tablas y falla cerrado si el contrato de esquema no está instalado. Preflight consulta sólo catálogos dentro de transacción READ ONLY.
- Marco visual de plataforma, navegador desktop/móvil y bandeja CP separados por autoridad. Acceso previo por INFORME conservado.

## Archivos del alcance

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261014_amex_sat_budget_direct.sql`
- `database/migrations/20261014_amex_sat_budget_direct_rollback.sql`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-sat-budget-direct-20261015-acceptance.md`
- `docs/roadmap/amex-sat-budget-direct-20261015-build-report.md`
- `docs/roadmap/amex-sat-budget-direct-20261015-coverage.json`
- `docs/roadmap/amex-sat-budget-direct-20261015-spec.md`
- `docs/roadmap/amex-sat-budget-direct-20261015-story.md`
- `docs/roadmap/amex-sat-budget-direct-20261015-validation.md`
- `docs/roadmap/samchat-convergence-register.md`
- `scripts/check_amex_direct_schema.py`
- `src/devnous/gastos/amex_direct_models.py`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_accounting_routes.py`
- `src/devnous/gastos/routes/admin_amex_direct_routes.py`
- `src/devnous/gastos/routes/admin_amex_statement_routes.py`
- `src/devnous/gastos/routes/admin_budget_ui.py`
- `src/devnous/gastos/routes/dependencies.py`
- `src/devnous/gastos/services/access_control_service.py`
- `src/devnous/gastos/services/amex_accounting_cut_service.py`
- `src/devnous/gastos/services/amex_cut_export_service.py`
- `src/devnous/gastos/services/amex_direct_accounting_service.py`
- `src/devnous/gastos/services/amex_direct_budget_service.py`
- `src/devnous/gastos/services/amex_direct_common.py`
- `src/devnous/gastos/services/amex_direct_context_service.py`
- `src/devnous/gastos/services/amex_direct_intake_service.py`
- `src/devnous/gastos/services/amex_direct_matching_service.py`
- `src/devnous/gastos/services/amex_direct_schema_contract.py`
- `src/devnous/gastos/services/amex_direct_schema_service.py`
- `src/devnous/gastos/services/amex_direct_status_service.py`
- `src/devnous/gastos/services/amex_recognition_service.py`
- `src/devnous/gastos/services/amex_statement_schema_service.py`
- `src/devnous/gastos/services/amex_statement_workflow_service.py`
- `src/samchat/budgets/service.py`
- `tests/acceptance/test_amex_sat_budget_direct_postgres.py`
- `tests/browser/test_amex_direct_workflow.py`
- `tests/integration/test_amex_approved_informe_schema_postgres.py`
- `tests/integration/test_amex_direct_backend_postgres.py`
- `tests/integration/test_amex_direct_schema_postgres.py`
- `tests/integration/test_amex_shared_recognition_postgres.py`
- `tests/integration/test_amex_statement_import_postgres.py`
- `tests/integration/test_amex_statement_workflow_postgres.py`
- `tests/unit/gastos/test_amex_direct_access_policy.py`
- `tests/unit/gastos/test_amex_direct_budget_display.py`
- `tests/unit/gastos/test_amex_direct_routes.py`
- `tests/unit/gastos/test_amex_direct_schema_admission.py`
- `tests/unit/gastos/test_amex_direct_schema_cli.py`
- `tests/unit/gastos/test_amex_direct_source_service.py`
- `tests/unit/gastos/test_amex_direct_startup_boundary.py`
- `tests/unit/private_plugin/test_inventory.py`
- `tests/unit/test_amex_direct_budget_integration.py`

## Verificación y evidencia

| Lane | Resultado final observado | Evidencia local |
| --- | --- | --- |
| Regresión AMEX unitaria completa y presupuesto, 35 archivos | **767 PASS**, 22.61s | `/tmp/amex-direct-root-final.log` |
| Backend + aceptación32 + exportadores legacy, conjunto solapado con unitarios | **132 PASS**, 144.97s | `/tmp/amex-direct-backend-current-tests.log` |
| Owner SQL: constraints, races, retenciones, edición y rollback | **40 PASS**, 53.77s | `/tmp/samchat-amex-direct-schema-tax-prior-final-20261015.log` |
| CLI y admisión estricta del SQL final | **48 PASS**, 4.06s; también incluidos en los unitarios | `/tmp/amex-direct-admission-final.log` |
| Frontend final | **113 HTTP PASS**, 12.95s; también incluidos en los unitarios | `/tmp/amex-direct-frontend-http-final.log` |
| Chromium aislado desktop/móvil/CP | **3 PASS**, 10.17s | `/tmp/amex-direct-browser-final.log` |
| Inventario y navegación | **11 PASS**, 64.46s | `/tmp/amex-direct-inventory-tests.log` |
| Cobertura modificada consolidada actual | **90.65% = 2008/2215** instrucciones modificadas; 25 archivos Python, cero sin medir | `amex-sat-budget-direct-20261015-coverage.json` |
| Todos los módulos nuevos | Black/isort/Flake8/Mypy **PASS**, 12 archivos | `/tmp/amex-direct-new-source-quality-final.log` |
| Inventario rutas regenerado tras versionar archivos | 600 rutas, 573 live static, 44 acciones, cero parse gaps | `docs/private-plugin/route-inventory.json`, `route-matrix.csv` |
| Validación independiente final | **0 Critical / 0 Important / 0 Minor abiertos** | `amex-sat-budget-direct-20261015-validation.md` |

Los conteos de lanes se solapan; no constituyen suma de casos únicos. PostgreSQL16 efímero local puerto55471, schemas sintéticos UUID, SQL real de owners. Transporte SAT sustituido por pruebas; sin consulta productiva ni XML/filas de negocio reales. Browser usa owners sintéticos. El pago verifica servicios existentes, no una transferencia bancaria real ni UAT.

Comandos de cada lane y sus entornos se detallan en los recibos citados. Intérprete tests `/root/samchat/.venv/bin/python`; `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps`. Formato/tipos/lint usan herramientas ya instaladas en `/srv/samchat/venvs/release-598e7a286/bin/python`, sin modificar dependencias de producción. `-o addopts=''` permite medir selecciones parciales; el gate se aplica a cobertura consolidada de líneas cambiadas, incluyendo archivos nuevos sin staging, contra el commit base.

## Calidad y fallos atribuidos

- Nuevos módulos: formato Black/isort, Flake8 conforme ignores existentes del pyproject y Mypy pasan en los12archivos. JS real extraído/nodecheck pasa. Los owners compartidos conservan deuda heredada: no se afirma Mypy/lint global verde.
- Owners compartidos de presupuesto: Mypy conserva exactamente 49 errores del baseline en 2 archivos; comparación normaliza únicamente desplazamientos de línea. Flake8 conserva exactamente 3 hallazgos heredados (F401/F811/F841). No ampliar esta entrega para reparar deuda previa. Evidencia `/tmp/amex-direct-quality-comparison.json`, `amex-direct-{baseline,root}-{mypy,flake8}.log`.
- Playwright sync seguido por legacy async en el mismo proceso provoca conflicto de event loop del harness. Nuevos111 y legacy32 pasan en procesos separados. No se presenta el experimento fallido como verde.
- Fallos iniciales introducidos: orden de flush frente a triggers, guard de mes heredado, assignments históricos leídos como actuales, tipo nuevo del catálogo y casos de tests equivocados: corregidos con regresiones. Los tres hallazgos de revisión quedaron corregidos y revalidados, como se detalla abajo.

## Revisión independiente y correcciones

Skill `implementation-validation`, validador de sólo lectura; recibo versionado `amex-sat-budget-direct-20261015-validation.md`. Tres hallazgos importantes corregidos dentro de la especificación y revalidados:

1. La fecha explícita exige fila real de período contable OPEN, bloqueando ausencia o cierre sin modificar reglas legacy.
2. Retenciones fiscales reales pueden confirmar el journal completo; neto de tarjeta separado de créditos fiscales. El resolver existente determina cuentas; su resultado se materializa en el override existente con auditoría dentro del mismo savepoint, sin sobrescribir conflictos ni cambiar versión operativa. SQL contrasta impuesto/importe/cuenta con evidencia canónica; alterar simultáneamente proof y líneas tampoco permite una cuenta arbitraria. Inyección de error después de materializar override/event revierte todo y permite retry correcto.
3. La edición se valida contra fecha económica y fecha contable usando la regla existente de año calendario, sin mover fechas o partidas. Mensajes de bloqueo explican responsable y condición.

El receipt previo requiere identidad persistida exacta. Replay del mismo anchor/corte devuelve el resultado existente. Un CSV nuevo con un CFDI usado permanece como excepción si carece de asociación canónica; no se infiere por importe/fecha/CFDI ni se añade otra arquitectura de asociación. La rama defensiva SQL coincide con el helper de proof real.

Un ciclo de pruebas concurrente con los últimos cambios SQL produjo 1FAIL/38PASS por bodyhash de admisión todavía anterior. Fue un fallo transitorio causado por el cambio, no infraestructura. El catálogo se regeneró exclusivamente desde PostgreSQL sintético, se congeló SQL y se repitió:40PASS. Ninguna verificación final depende de aquel ciclo fallido.

La revisión automática rechazó una propuesta de borrar assignments anteriores por mutación potencialmente irreversible sin autorización específica. No se aplicó. La solución preserva filas/eventos históricos e invalida sólo la decisión vigente; no requiere ese borrado.

## Canon, desviaciones y activación pendiente

Enmiendas exactas de producto e ingeniería aprobadas, con motivo/fecha/evidencia; hashes actualizados en registro. Sweep permanece intacto. No se declara implementación desplegada ni aceptación de negocio. No se pretende cambiar el canon adicional sin revisión humana.

Sin desviaciones de alcance autorizadas. Antes de activar deben definirse configuración fiscal por tarjeta, receptores/entidades, proyectos y UUID/scopes reales, además de la migración owner-run autorizada. `AMEX_DIRECT_AUTHORIZED_CONTEXTS` vacío no concede permisos ni inventa entidades. No se poblaron dichos datos. Los14 casos históricos y el trabajo RFC separado no se alteran.

Pendientes al cierre: aprobación final del diff; autorizaciones explícitas de publicación, migración y despliegue; selección de release que no incluya promociones ajenas; UAT Finance/CP autenticado con SAT/COI y pago reales. Backup/verificación de schema, preflight y rollback corresponden a la fase productiva, aún no ejecutada.

## Comandos finales reproducibles y recibos

```bash
# Pruebas unitarias: test_amex*.py más presupuesto y RFC, 35 archivos en total.
PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps \
COVERAGE_FILE=/tmp/amex-direct-root-final.coverage \
/root/samchat/.venv/bin/python -m pytest \
  tests/unit/gastos/test_amex*.py tests/unit/test_amex_direct_budget_integration.py \
  tests/unit/test_budget_service.py tests/unit/gastos/test_coi_poliza_exporter_rfc.py \
  -o addopts='' --cov=src/devnous/gastos --cov=src/samchat/budgets \
  --cov=scripts --cov-report= -q --tb=short

# Backend+aceptación+exportadores: TEST_AMEX_DATABASE_URL señala exclusivamente
# al PostgreSQL sintético local55471, test_user/devnous_test.
PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps \
COVERAGE_FILE=/tmp/amex-direct-backend-current.coverage \
/root/samchat/.venv/bin/python -m pytest \
  tests/unit/gastos/test_amex_direct_source_service.py \
  tests/integration/test_amex_direct_backend_postgres.py \
  tests/acceptance/test_amex_sat_budget_direct_postgres.py \
  tests/unit/gastos/test_amex_cut_exports.py \
  -o addopts='' --cov=src/devnous/gastos --cov-report= -q

# Browser en proceso propio para evitar conflicto del event loop del harness.
PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps \
COVERAGE_FILE=/tmp/amex-direct-browser-final.coverage \
/root/samchat/.venv/bin/python -m pytest tests/browser/test_amex_direct_workflow.py \
  -o addopts='' --cov=src/devnous/gastos --cov-report= -q --tb=short
```

Cobertura: `/tmp/amex_direct_changed_coverage.py` usa Coverage API y difflib contra base `d8c9fda23ed1b1bb1a68a08bc524fedbdb6d4e5f`, incluyendo untracked sin staging; combina exclusivamente esos tres archivos actuales, todos posteriores al congelamiento de fuente. No combina ejecuciones con posiciones anteriores al patch. `diff-cover` no está instalado en los entornos locales consultados; no se instaló ni se afirma haber ejecutado el gate GitHub. La medida local queda reproducida en el JSON versionado. CI remoto, suite global del repositorio, auditoría global de dependencias y UAT productivo no se ejecutaron en esta fase.

Hashes finales:

- SQL owner final: `5834a6b17ef2a595ceddb7f95d8931d1effb81b479c9153f9c07945eb2c24d7b`.
- Rollback: `8c4d9c4b40c98c8724ed446fc1c7486359c3d95a3e798e7554f579cdfa22e4a8`.
- Producto: `7062756077f455741f03027b335f0be6627476c23f9f0b4ab8744f0a9608c99e`.
- Ingeniería: `bd011d2d33deaea655e4bc3f863e61610454520544d477e1cba1560f6fabd47c`.
- Sweep intacto: `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`.

Se verificó que los hashes de canon coinciden con el registro. `git diff --check` pasa. El contenedor PostgreSQL16 desechable `samchat-amex-direct-tests-20261015` fue cerrado al terminar; ningún servicio productivo fue modificado. El artefacto `.coverage` inicial se preservó fuera del diff en `/tmp/amex-direct-initial-worktree.coverage`.

## Aprobación de publicación

El usuario respondió «aprobado» el2026-10-15 a la solicitud explícita de aprobar este cambio final y autorizar commit, push, PR y merge. La autorización conserva el alcance de historia/spec/canon ya revisados y no incluye migración productiva ni despliegue. Se verificó nuevamente integridad de canon y que origin/main sigue en la base d8c9fda23 antes de publicar. Los resultados remotos y el merge se acreditarán por sus receipts, sin anticiparlos.

Precommit: el chequeo del índice incluyó por primera vez el SQL nuevo y detectó espacios finales en el DDL generado. Se retiraron sin cambiar ningún cuerpo de función (`$$...$$` byte a byte idénticos), contrato, línea lógica ni objeto de esquema. El SQL de la ejecución40PASS tenía SHA `2ae3c138d07f5c0b6e0642d355c7c33a543261a6593bd3c0e10b441872edbf16`; el hash final tras esta limpieza se registra arriba. El guard de admisión conserva sus hashes y CI verificará de nuevo el archivo publicado.

## Corrección de compatibilidad durante publicación del PR #488

El primer ciclo remoto del commit938942fbb pasó política, seguridad, asistente y browser, pero falló la reproducción del inventario en la suite aislada private-plugin. El generador usa `git ls-files`: la preparación anterior al commit había omitido11archivos fuente aún no versionados. Tras versionarlos, se regeneraron ambos artefactos:464fuentes y600rutas. Las590rutas anteriores permanecen sin alteraciones ni retiros; se agregan10de AMEX directo, sin cambiar44acciones,37capabilities, permisos ni evidencia de montaje. La limitación estática del generador para probar el montaje anidado permanece explícita; no se inventa evidencia live. Reproducción/navegación:11PASS25.27s. Este fallo fue causado por la preparación del cambio, no por infraestructura.

La regresión ampliada encontró fixtures históricos que copiaban por ORM las9tablas directas owner-run o instalaban sólo DDL anterior y luego solicitaban admisión actual. Se corrigió exclusivamente la preparación en cuatro archivos: `test_amex_shared_recognition_postgres.py`, `test_amex_statement_import_postgres.py`, `test_amex_statement_workflow_postgres.py` y el test puntual de salud actual en `test_amex_approved_informe_schema_postgres.py`. Se excluyen tablas owner, se ejecuta la cadena real actual y el rollback respeta dependencias; no se relaja admisión, no se añade CASCADE ni se modifica código/SQL productivo o assertions de negocio. Los demás tests de migraciones históricas conservan su fixture original.

Verificación: **246 pruebas PostgreSQL históricas distintas aprobadas**:131en las3suites principales,80en otras5suites dependientes y35de esquema aprobado. Hubo1fallo previo de salud por CHECK histórico, corregido y revalidado con las35pruebas del archivo y1focal final. Logs `/tmp/amex-direct-legacy-fixtures-full.log`, `/tmp/amex-direct-legacy-dependent-fixtures-full.log`, `/tmp/amex-direct-legacy-current-health-after.log`, `/tmp/amex-direct-legacy-current-health-final.log`; reporte `/tmp/amex-direct-legacy-fixture-build-report.md`. Black/Flake8 cuatro archivos y diff-check pasan. Isort de workflow conserva exactamente la deuda de HEAD; los otros3pasan. El código productivo y su cobertura90.65% no cambiaron en este ajuste.
