# Conciliación de presupuestos aprobados — informe de construcción

Fecha: 2026-11-06. Estado: implementación, pruebas y ensayo restaurado final terminados; pendiente checkpoint humano del diff/lote. Sin commit, push, PR, merge, despliegue ni aplicación productiva de este cambio.

## Resultado y alcance

Se añade una conciliación explícita hacia líneas de gasto existentes de una versión financiera aprobada. Incluye vista previa de impacto íntegro, confirmación con CSRF y nonce, motivo, comprobación fresca del actor/capacidad, transacción SERIALIZABLE e idempotencia acreditada por auditoría. El core comparte la proyección canónica y conserva el fallback existente. El writer genérico no AMEX mantiene draft/reforecast y ahora refresca sus bloqueos antes de validar.

El operador utiliza el mismo core y un manifest fijo de 16 propuestas (6 Liga, 10 Copa). La aprobación de historia/especificación no permite aplicarlas en producción: exige un recibo externo del diff y lote concretos, hashes de fuentes, actor vigente, release servido limpio y respaldo/preview frescos. No hay migraciones, dependencias, roles, jobs ni cambios fiscales nuevos.

## Archivos

- Core: `src/samchat/budgets/reconciliation.py` y cambio acotado en `service.py`.
- Web: `src/devnous/gastos/routes/admin_budget_routes.py` y `admin_budget_ui.py`.
- Operador y manifest: `scripts/reconcile_approved_budget_movements.py`, `config/budget_approved_reconciliation_20261106.json`.
- Tests nuevos de core, operador, rutas, render, navegador y PostgreSQL; adaptación mínima de invocación/fixtures de GET en `test_admin_budget_route_safety.py` y `test_tournament_weekly_overlay_postgres.py`.
- Historia/spec versionadas, este informe e inventarios privados de rutas regenerados (626 rutas, 590 estáticas vivas, 44 acciones canónicas, 0 errores de parseo).

Los candidatos MAIN (base223bda737) y SELECTIVE (base72e884752) contienen los mismos hunks de funcionalidad y conservan sus diferencias previas de CxC. El ensayo integral utilizó SELECTIVE, compatible con el release servido. El checkout raíz y sus seis modificaciones ajenas se preservaron.

## Aceptación en copia restaurada

Evidencia privada: `tests/artifacts/dashboard-technical-closeout-20261106/acceptance/final-selective-03/acceptance-matrix.md`.

- ASGI registrado con sesión firmada, core, AsyncEngine y UoW reales; autenticación sintética respaldada por empleado activo del clon. CSRF y alteraciones rechazados sin escrituras; no se confirma/revierte la sesión de autenticación.
- Hash obsoleto y fallos tras INSERT1/17: rollback exacto de 19 tablas. Lote completo: 16 BMA nuevos y un evento auditado, conservando los 50 BMA anteriores y 17 tablas protegidas.
- Lectores canónicos reales: 370 movimientos, sólo cambian destino/estado de conciliación en los16 seleccionados;354 restantes idénticos. Períodos, kinds e importes íntegros;11 pending_accounting conservados.
- 263 líneas, estado aprobado, nota Borrador, ingresos y semanas40/52/52 intactos. Totales: Liga3,686,827.60; HWC30,384,939.78; Copa17,471,291.92 MXN. HWC conserva0sinlínea y sus clasificaciones históricas sin intervención.
- Repetición del lote y recibo web vencido devuelven el resultado anterior sin DML; rechazo de apply productivo sin aprobación antes de abrir engine/entorno productivo.

La copia final contiene66BMA/297audits. Se verificó el SHA del respaldo antes de restaurar únicamente la base desechable autorizada. No se escribió producción. Este ensayo no sustituye UAT autenticada ni aceptación financiera.

## Checks y límites

Resultado final: **691 pruebas aprobadas, 0 omitidas**, más **3 pruebas Chromium aprobadas**. Cobertura diferencial completa src+scripts: **94%** (742 líneas cambiadas, 41 sin cubrir), superior al umbral85%. Recibos privados `combined-final-tests-v2.log`, `browser-final-tests.log`, `diff-coverage-final.log` y `final-style-checks.json`.

Comandos ejecutados, con cwd MAIN y runtime `/srv/samchat/venvs/release-598e7a286/bin/python`:

```text
PYTHONPATH=src COVERAGE_FILE=/tmp/dashboard-approved-reconciliation-main.coverage SAMCHAT_WEEKLY_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55479/postgres python /tmp/run-approved-reconciliation-final-suite.py
PYTHONPATH=src /root/samchat/.venv/bin/python -m pytest -q tests/browser/test_approved_budget_reconciliation.py
/tmp/hwc-diff-cover-tools-526/bin/diff-cover /tmp/dashboard-approved-reconciliation-main.xml --compare-branch=origin/main --fail-under=85
python -m black --check <9 archivos Python nuevos>
python -m isort --check-only <9 archivos Python nuevos>
python -m flake8 --max-line-length=88 <9 archivos Python nuevos>
python scripts/ci/check-registration-operational-surface.py
python scripts/ci/check-accepted-regressions.py
git diff --check
```

La lista exacta de22archivos pytest, el runner y argv de estilo se conservan en el paquete privado (`suite-paths.json`, `run-final-suite.py`, `final-style-checks.json`). El ensayo SELECTIVE03 usa los harnesses y comandos exactos archivados junto a su matriz; PostgreSQL/readers/ASGI reales con el core final congelado. PostgreSQL real para concurrencia de confirmaciones, actualización de fuente/destino y writer draft obsoleto; suites de regresión documental/semanal/autorización y navegador1366/390.

Black/isort/flake8 focales y diferencial de fuentes heredadas; gates de superficie operativa y regresiones aceptadas en ambos candidatos PASS. No se afirma limpieza global de lint/typecheck: el servicio conserva154diagnósticos heredados con0nuevos y los archivos web tienen formato heredado no conforme a Black. Mypy/global lint no ejecutados por alcance/deuda conocida. Warnings: importación previa de anyio y datetime.utcnow heredado.

El primer combinado detectó una llamada directa de prueba sin Request nuevo; se adaptó sólo ese test y su esquema parcial con stubs de consultas ajenas al lector semanal, manteniendo sus lectores PostgreSQL y aserciones. La autorización/gobernanza sí se prueba en rutas registradas y el ensayo restaurado. El harness inicial rechazó CTE/comentarios SELECT: se corrigió sólo su guard privado, manteniendo transacciones READ ONLY, y se repitió el ensayo completo. Una ejecución de formato en cwd raíz se restauró byte por byte tras verificar AST/hash de sus tres archivos propios; las seis ediciones ajenas se conservaron. No queda efecto de esos incidentes en el alcance.

## Revisión independiente y corrección

Primera pasada: 0 hallazgos críticos, 0 importantes y 1 menor. Se ordenó el catálogo por UUID antes del fingerprint para evitar falso drift ante distinto orden SQL. Una regresión conductual invierte el orden de consulta y confirma exitosamente el SHA original. 53 focales pasan (42 unitarias y 11 PostgreSQL), cobertura del core95.35%. No cambia proyección, elegibilidad, autoridad ni economía. El combinado final691PASS, la aceptación restaurada SELECTIVE03 completa y la revalidación independiente confirman el ajuste con el nuevo hash congelado. No quedan hallazgos de implementación abiertos.

La medición focal que incluyó todas las2770sentencias del servicio heredado no alcanzó el umbral global por denominador ajeno; ninguna prueba falló. La medición acotada del core supera85%; el gate aprobado exige cobertura diferencial del diff completo src+scripts y se calcula aparte sobre la suite conjunta.

## Decisiones pendientes

Las13propuestas bloqueadas (8sinlínea,5conceptos retirados) permanecen sin aplicar. Siguen pendientes las autorizaciones fiscales/crosswalk/exclusividad del expediente85cuentas/26renglones y las fuentes contables retenidas; no se fabrican bindings aprobados ni balances de septiembre. No se reescriben pagos, pólizas, fuentes, importes, calendarios ni estados.

No hay desviación de comportamiento/arquitectura ni ampliación de autoridad respecto a la spec aprobada. La adaptación de los dos tests existentes es mantenimiento mínimo por el nuevo Request/consulta del GET. Límite de formatos: no existe exportador PDF de movimientos. El XLSX de revisión exporta metadatos de líneas, fase, cuenta y autorizado, sin IDs ni medidas por movimiento. Tres casos nuevos prueban la proyección canónica real, HTML y lectura de ese XLSX; la paridad de grano por movimiento en XLSX/PDF es N/A, sin crear producto fuera de alcance.

**Canon unchanged:** acción explícita, acotada y auditada sobre vínculos auxiliares; mantiene inmutabilidad económica, estado, owners fiscales/pagos y autoridad separada AMEX. Los tres SHA del canon coinciden con su registro en ambos candidatos; sin cambios automáticos de canon.

Pendiente antes de publicación: checkpoint humano separado de diff/lote y efectos externos. Revisión independiente: 0 hallazgos críticos/importantes; M1 menor corregido, probado y revalidado. Evidencia privada `validation-findings.md`. El recibo productivo se crea exclusivamente después de esa respuesta humana y se vincula al árbol SELECTIVE y hashes revisados.
