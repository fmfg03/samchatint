# Informe de construcción: recorrido AMEX y separación de pendientes

Fecha: 2026-10-13; cierre de auditoría 2026-10-14. Worktree aislado `amex-statement-workflow-20261013`, base `4b971ab3ff4576216a7009aaf883d1351b7c7eb2`.

Estado: implementación y aceptación automatizada verificadas; revisión funcional independiente sin defectos pendientes. Calidad global parcial por deuda heredada. Audit externo de dependencias aprobado tras autorización de continuación; publicación Git inicial en PR #482 completada; correcciones de review pendientes de push/CI/merge al redactar este cierre. Migración, despliegue y UAT productiva pendientes. No se reutilizan aprobaciones de PR480 para estos efectos.

## Trabajo completado

- Finanzas → AMEX → Estados ofrece carga, resolución de pendientes y confirmación/descarga por INFORME completo. Conserva selección y regreso desde detalle, edición y conciliación, con allowlist local.
- Política compartida de atención por beneficiario explícito: Odilón fuera de colas/selectores/contadores manuales; otros, desconocidos y contradicciones visibles con motivo. Banco y controles financieros permanecen íntegros.
- Identidad auditable por archivo exacto, tarjeta/titular/período/actor. Replay serializado, conflictos de contexto y confirmación de carga adicional; filas omitidas explícitas, rollback completo ante errores DB.
- Totales desde receipts originales inmutables, separados por moneda. Pendientes operativos, asociación bancaria real y avance contable se muestran como hechos diferentes. Importe ausente permanece desconocido.
- Confirmación y vínculo de navegación transaccionales por corte canónico completo. Incorporación explícita sin repost; exportación desde snapshots congelados falla cerrada para todo el conjunto.
- Tres tablas owner-run con guards/FKs y protección de rollback poblado. Sin DDL runtime, backfill, reparaciones históricas, dependencias nuevas ni efectos productivos.
- Enmiendas exactas de producto/ingeniería explícitamente aprobadas y hashes actualizados. Sweep Canon unchanged: es evidencia histórica y esta entrega no acredita aceptación productiva.

## Pruebas y resultados

| Verificación | Resultado |
|---|---|
| Regresión unitaria general, excluyendo plugin/readonly separados | 5.123 PASS; 8 subtests PASS |
| AMEX + navegación/reporting, fuentes finales tras review | 552 PASS |
| Todas las integraciones AMEX, PostgreSQL 16 desechable | 228 PASS |
| Nueva migración y workflow PG, repetición final tras review | 29 PASS |
| Integración restante del repositorio | 89 PASS, 5 SKIP |
| Plugin offline/inventario, incluida repetición final | 135 PASS |
| Playwright desktop/mobile, recorrido y paginación reales sintéticos | 4 PASS |
| Aceptación independiente | 310 PASS en grupos separados; criterios 1–7 cubiertos |
| Validación funcional independiente | 72 PASS; sin hallazgos funcionales pendientes |
| Líneas modificadas contra origin/main | 892/960 cubiertas, 92,92%; gate ≥85% aprobado |
| Seis archivos nuevos, flake8 estricto 88 y mypy con imports silent | Sin errores |
| Legacy frente a base, errores nuevos/líneas modificadas | Cero; deuda total flake8 4.148→4.134, mypy 1.048→1.041 |
| Regresiones aceptadas, superficie de registro, packaging | Aprobados |
| Bandit high/high | Coincide exactamente con baseline aceptado |
| git diff --check; canon SHA/registro | Aprobados |
| pip-audit externo | PASS: sin vulnerabilidades fuera de 5 excepciones conocidas; salida 0 |

Los grupos se solapan; no sumar sus cifras como pruebas únicas. La suite general precedió los últimos ajustes locales; los archivos afectados se volvieron a verificar con las suites finales. La cobertura combina solo AMEX unitarios, nuevos PG y navegador sobre fuentes finales. Se omitió exclusivamente `quick_amex_real_render_block`, pseudoarchivo de fixture `exec`, para generar XML; no se excluyó código versionado.

## Comandos ejecutados

Usando intérpretes y dependencias ya disponibles, sin instalar dependencias productivas:

```text
pytest tests/unit --ignore=tests/unit/private_plugin --ignore=tests/unit/readonly_gateway --timeout=60 --cov=src --cov=copa_telmex_dashboard --cov-fail-under=0 --junitxml=/tmp/samchat-amex-workflow-unit.xml
pytest tests/unit/gastos/test_amex* tests/unit/gastos/test_documentos_todos_reporting.py --timeout=120 --cov=devnous.gastos --cov-fail-under=0
pytest tests/integration/test_amex* --timeout=120 --cov=devnous.gastos --cov-fail-under=0
pytest tests/integration/test_amex_statement_import_postgres.py tests/integration/test_amex_statement_workflow_postgres.py --timeout=120 --cov=devnous.gastos --cov-fail-under=0
pytest tests/integration --ignore=tests/integration/private_plugin --ignore-glob=tests/integration/test_amex* --timeout=120
pytest tests/browser/test_amex_statement_workflow.py --timeout=120 --cov=devnous.gastos.routes.admin_amex_statement_routes --cov-fail-under=0
python scripts/private_plugin/inventory.py
coverage run --source=src/samchat/private_plugin scripts/private_plugin/run_offline_tests.py
coverage combine --keep <tres archivos finales>
coverage xml --omit=*/quick_amex_real_render_block
diff-cover <XML final> --compare-branch=origin/main --fail-under=85
flake8 <seis módulos nuevos> --max-line-length=88
mypy --follow-imports=silent --ignore-missing-imports <seis módulos nuevos>
python scripts/ci/check-pytest-baseline.py --junit <XML unitario> --baseline .github/pytest-unit-failures.txt
python scripts/ci/check-accepted-regressions.py
python scripts/ci/check-registration-operational-surface.py
python scripts/check_runtime_packaging_consistency.py
bandit -r src copa_telmex_dashboard.py -lll -iii -f json
python scripts/ci/check-bandit-baseline.py --report <JSON> --baseline .github/bandit-high-confidence-baseline.txt
git diff --check
sha256sum <tres canons>
PYTHONPATH=/tmp/samchat-amex-approved-security-tools python -m pip_audit --requirement requirements.txt --ignore-vuln PYSEC-2026-311 --ignore-vuln PYSEC-2026-3813 --ignore-vuln PYSEC-2026-3814 --ignore-vuln PYSEC-2026-3815
```

## Fallos conocidos y límites

- Lint/tipos globales siguen fallando por deuda heredada, comparada mediante mensajes y líneas modificadas; no hay errores nuevos atribuibles.
- Cinco integraciones no AMEX omitidas: módulos legacy Kafka/Redis ausentes y tres suites Copa/registro requieren socket PG efímero específico. No se afirma verificación de esos dominios.
- Plugin PostgreSQL aislado y acceptance readonly gateway no se reejecutaron: no se modificó su código, SQL ni permisos; offline/inventario sí se ejecutaron. CI remota posterior permanece pendiente.
- Un comando independiente mezcló Playwright antes de PG y produjo 55 setup ERROR (`Runner.run()` dentro de loop activo). Ambos grupos pasaron en procesos separados; infraestructura runner, no aserciones financieras fallidas.
- Errores transitorios de fixtures, formato y tipos atribuibles al cambio se corrigieron y sus checks se repitieron. Deprecaciones existentes permanecen visibles en logs.
- La revisión automática rechazó inicialmente `pip-audit` por la salida externa de metadatos. Después de explicar el efecto y solicitar permiso, el usuario indicó «continua» el 2026-10-14. La ejecución autorizada terminó con código 0: `No known vulnerabilities found, 5 ignored`. Recibo local: `/tmp/samchat-amex-workflow-pip-audit-20261014.log`. No hubo cambios de dependencias ni código.
- `origin/main` local avanzó a `9c89ade7a` por PR481 de presupuestos en archivos ajenos; la base AMEX permanece intacta, cobertura se comparó con ese main. Revalidar promoción/CI sobre el merge real.

## Desviaciones y riesgos posteriores

Sin desviaciones funcionales de la historia/especificación aprobadas. Correcciones de navegación, motivos, totales y tipos están dentro de alcance. Guard UPDATE/DELETE de cut-link se verifica mediante catálogo/cuerpo de función compartida; aún no tiene negativo dedicado sobre un cut-link creado. El renderer usa HTML canónico: futuros cambios deben mantener regresiones de navegación.

Seguir [preparación de release](amex-statement-workflow-20261013-release.md). Producción, los nueve informes reales, los 14 históricos protegidos y aceptación en COI requieren autorización/recibos/UAT separados. PR #482 publicado; merge pendiente de las correcciones y sus checks. No se ha migrado ni desplegado esta entrega.

## Archivos cambiados

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261013_amex_statement_workflow.sql`
- `database/migrations/20261013_amex_statement_workflow_rollback.sql`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-statement-workflow-20261013-spec.md`
- `docs/roadmap/amex-statement-workflow-20261013-story.md`
- `docs/roadmap/samchat-convergence-register.md`
- `scripts/check_amex_statement_schema.py`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_accounting_routes.py`
- `src/devnous/gastos/routes/admin_amex_statement_routes.py`
- `src/devnous/gastos/routes/user_routes.py`
- `src/devnous/gastos/services/amex_attention_policy.py`
- `src/devnous/gastos/services/amex_statement_import_service.py`
- `src/devnous/gastos/services/amex_statement_schema_service.py`
- `src/devnous/gastos/services/amex_statement_workflow_service.py`
- `tests/browser/test_amex_statement_workflow.py`
- `tests/integration/test_amex_statement_import_postgres.py`
- `tests/integration/test_amex_statement_workflow_postgres.py`
- `tests/unit/gastos/test_amex_accounting_cut_routes.py`
- `tests/unit/gastos/test_amex_approved_informe_routes.py`
- `tests/unit/gastos/test_amex_attention_policy.py`
- `tests/unit/gastos/test_amex_card_identity_routes.py`
- `tests/unit/gastos/test_amex_statement_import_service.py`
- `tests/unit/gastos/test_amex_statement_legacy_integration.py`
- `tests/unit/gastos/test_amex_statement_routes.py`
- `tests/unit/gastos/test_amex_statement_workflow_service.py`
- `tests/unit/gastos/test_documentos_todos_reporting.py`
- `docs/roadmap/amex-statement-workflow-20261013-release.md`
- `docs/roadmap/amex-statement-workflow-20261013-build-report.md`

## Cierre de observaciones de PR #482 — 2026-10-14

La primera cabeza actualizada con main (`274166dcc`) pasó los checks remotos, incluido Required PR gate. Antes del merge se recibieron tres observaciones P2 y se verificaron directamente; las correcciones permanecen dentro de la historia/especificación y autoridad de publicación aprobadas:

1. Tablero sin límite: el listado ahora consulta 20 importaciones por página (límite interno máximo 50), orden estable por fecha/id y lookahead de una fila. Solo construye detalles de la página y ofrece navegación al historial, incluso desde una página vacía. No se ocultan cargas antiguas.
2. Cargo cancelado: estado explícito `cancelado` y pendiente operativo; importe original y asociación histórica conservados. PostgreSQL prueba los casos sin vínculo y con vínculo legado legítimo. La protección existente de `report_bridge` impide cancelar su fuente económica y se conserva: test de SQLSTATE23514 y rollback, sin bypass ni modificación del owner.
3. Snapshot inválido: el servicio público normaliza `ValueError` anticipado a `WorkflowConflict`; el endpoint devuelve HTTP409 sin archivo parcial ni detalle interno. El test alcanza dos snapshots y comprueba que el generador no corre si el segundo falla; ASGI usa el convertidor real sobre evidencia inválida.

Recibos finales: 552 regresiones AMEX/reporting; 29 PG de importación/workflow; 4 Playwright; 135 plugin offline. Backend propio 61 PASS (48 unidades y 13 PG); frontend propio 39 PASS; grupos solapados. Validación independiente focalizada: 62 PASS. Lint/tipos estrictos de los dos módulos corregidos aprobados. Inventario actualizado; diff check aprobado. Cobertura modificada 892/960=92,92%.

La primera recombinación posterior al review omitió las mediciones de migración/importación y falló con 81%. Se ejecutaron las 29 pruebas PG y se recombinó el conjunto correcto; el gate ≥85% aprobó. El fallo era del conjunto de evidencia, no una corrección de lógica omitida. Una prueba presupuestal aislada falló en colección por orden de import circular de código ajeno; la ejecución con orden de carga de la suite pasó 82 tests, sin ampliar alcance para editar presupuestos.

Canon unchanged para estas correcciones: el recorrido, ownership, granularidad contable, schema, política de atención y límites de autoridad siguen siendo los de las enmiendas exactas aprobadas. CI se repetirá sobre el commit corregido antes de resolver el merge; esta nota no acredita su finalización ni despliegue.
