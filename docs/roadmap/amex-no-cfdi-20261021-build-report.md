# AMEX directo Sin CFDI — reporte de construcción

Fecha: 2026-10-21. Estado: implementación y aceptación técnica terminadas; revisión independiente sin hallazgos pendientes. Aprobación humana final y publicación/migración/despliegue pendientes.

## Alcance y aprobación

Historia y especificación aprobadas explícitamente por el usuario. Se implementa únicamente la decisión auditada Sin CFDI para cargos nuevos, con importe bancario completo como gasto contra pasivo AMEX y sin movimientos fiscales estimados. PASE, carga sin previsualización, multiempresa, pagos y registros históricos quedan fuera.

Base: `fac1d1db361f7dc27cd94409f801afef3e9881c7`. Worktree aislado `amex-no-cfdi-20261021`; checkout principal preservado. No dependencias nuevas ni cambios productivos.

## Cambios

- Decisiones append-only de declaración y revocación, motivo obligatorio, confirmación, idempotencia y versiones; exclusión de tratamientos incompatibles.
- Selección por lote en la pantalla existente, mensajes y evidencia auditada; búsqueda de factura requiere revocación explícita.
- Presupuesto conserva el importe bancario completo; clasificación y asignación no invalidan la declaración.
- Cobertura `new_no_cfdi` usa consumo, representación, corte y póliza existentes: dos movimientos exactos de gasto/pasivo por cargo.
- Exportación congelada XLSX/CSV/ZIP con trazabilidad, sin identidad fiscal artificial.
- Migración owner-run con guards y contrato exacto de metadata; startup DDL excluido. Rollback se niega si existe evidencia.
- Se incorporan sólo los textos exactos de canon aprobados (§10 de la especificación); registro actualizado. Sweep: Canon unchanged, por ser un reporte histórico de su baseline.

## Evidencia de pruebas

- Regresión final de AMEX, inventario y exportadores RFC/descripción: **1,061 aprobadas**, 38 advertencias; `/tmp/amex-no-cfdi-final-unit-20261021.log`. Las advertencias del ciclo ORM ya sólo incluyen documentos/expense_reports heredados; se eliminó el ciclo nuevo de la referencia a decisiones.
- Contrato y guards SQL: **58 aprobadas** finales (41 admisión +17 PostgreSQL nuevas); `/tmp/amex-no-cfdi-schema-final.log`. Regresión anterior de esquema/owners: 97 aprobadas, con solapamiento; no sumar ambas corridas como pruebas únicas.
- Backend final contra DDL congelado: **39 aprobadas** (15 PostgreSQL +24 unitarias del owner). Ambos perfiles de signo, cadena e idempotencia, corte mixto fiscal/Sin CFDI, presupuesto una sola vez, rollback ante error tardío capturado, revocación/contexto y exportaciones.
- Backend: 175/197 líneas ejecutables nuevas/modificadas cubiertas, **88.83%**, con 64 unitarias focales adicionales a las39 anteriores (103 casos únicos dentro de esa medición); `/tmp/no-cfdi-backend-diff-coverage.json`.
- Servicio nuevo: **94%** (118/126 líneas ejecutables) en `/tmp/no-cfdi-backend-coverage.json`. Presupuesto: 24/25 líneas ejecutables del diff, **96%**. Rutas: 57/57 líneas del diff, **100%**; no sumar porcentajes de marcos distintos.
- Navegador Chromium con PostgreSQL y owners reales: **1 aprobada**; declarar2 → revocar2 → redeclarar2 → clasificación → CP asigna → preview READ ONLY → póliza4movimientos por $348 → actuals2 sin duplicados → XLSX/CSV/ZIP → formularios congelados. No se mockearon los owners financieros ni se usaron cargos productivos.
- Aceptación independiente: **13/13 aprobadas** en PostgreSQL, 24.34s; seis variantes de denegación de autoridad/scope, cinco carreras de transacciones, corte de cinco tipos y revocar → recuperar período → redeclarar. [Reporte](amex-no-cfdi-20261021-acceptance-report.md); `/tmp/amex-no-cfdi-independent-acceptance-20261021.log`. Cada carrera cubre una ejecución acotada, sin afirmar exploración exhaustiva de interleavings.
- Primera validación de sólo lectura detectó **Important I1**: multiplicidad de snapshots podía dejar un corte reconocido e inexportable. Se corrigió el guard de igualdad exacta entre encabezado, miembros, movimientos raíz y ledger; [revisión y cierre](amex-no-cfdi-20261021-validation-report.md). **I1 cerrado por revalidación independiente:** reproducción original rechazada atómicamente (1 prueba) y ocho variantes de manipulación aprobadas. Ningún hallazgo Critical/Important/Minor pendiente.
- Corrección I1: 66 pruebas aprobadas (41 admisión +24 SQL +1 corte mixto) y, tras cerrar el encabezado huérfano, 43 aprobadas (41 admisión +1 mutante huérfano +1 mixto). No sumar casos solapados. Logs `/tmp/amex-no-cfdi-bijection-final-20261021.log` y `/tmp/amex-no-cfdi-header-graph-final-20261021.log`.
- **Guard final congelado:** navegador +13 casos independientes repetidos sobre el DDL final, **14 aprobados**, 31.77s; `/tmp/amex-no-cfdi-final-affected-acceptance-20261021.log`. Inventario final regenerado y reproducibilidad aprobada, 1 prueba. Migración SHA256 `4fa8d42850e05db21c4b370771114f063f79c38f808564a44b5c6ab1c3e4ca7e` y contrato SHA256 `d48e131e75e5f9febf58c6245d3d27a2849222f78beaa40525b9f2d25e08b1e5`.

## Calidad estática y fallos heredados

Mypy presupuestal y esquema focal: aprobados. Mypy del conjunto de nueve servicios/ruta: **9 errores preexistentes**, 1 de retorno Any en sugerencias y8 de typing de recuperación en rutas. Se reprodujeron sobre archivos originales de `HEAD` (`/tmp/amex-no-cfdi-baseline-mypy-20261021.log`). No se amplió el alcance para repararlos.

Black/isort/flake8 de archivos del cambio: las comprobaciones globales conservan fallos preexistentes en `models.py` (formato/imports y3 F401) e isort del fixture original de aceptación. Mismos fallos reproducidos con el preimage `HEAD` (`/tmp/amex-no-cfdi-baseline-style-20261021.log`). Nuevos archivos y demás archivos modificados pasaron con la configuración de estilo del proyecto. `git diff HEAD --check` aprobado. No se declara aprobación global de estilo ni tipos.

Fallos intermedios de harness corregidos: sintaxis SQL durante construcción, trigger con resolución de columna heterogénea, fixture con eventos diferidos pendientes, y decodificación UTF8 equivocada del CSV CP1252 existente. Los fallos de construcción se conservan en sus logs; para la aceptación independiente, el log inicial fue sobrescrito y el reporte identifica expresamente una reconstrucción de sus dos errores de expectativa, conservándose los tracebacks originales en las salidas de herramientas. Las corridas finales identificadas arriba usan DDL congelado. No se cambió el contrato de codificación de COI.

## Comandos

Entorno existente: `/root/samchat/.venv/bin/python`; `PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps`.

```sh
python -m pytest -q --no-cov tests/unit/gastos/test_amex*.py tests/unit/test_amex*.py tests/unit/private_plugin/test_inventory.py tests/unit/gastos/test_coi_poliza_exporter_rfc.py tests/unit/gastos/test_coi_poliza_exporter_description.py
python -m pytest -q --cov=devnous.gastos.services.amex_direct_budget_service --cov-report=json:/tmp/no-cfdi-budget-coverage.json --cov-report=term --cov-fail-under=0 tests/unit/gastos/test_amex_direct_no_cfdi_budget.py tests/unit/test_amex_direct_budget_integration.py
python -m mypy --follow-imports=skip src/devnous/gastos/services/amex_direct_budget_service.py
python scripts/private_plugin/inventory.py
# PostgreSQL local desechable conforme al guard TEST_AMEX_DATABASE_URL:
python -m pytest -q --no-cov tests/integration/test_amex_direct_no_cfdi_backend_postgres.py tests/unit/gastos/test_amex_direct_no_cfdi_service.py
python -m pytest -q --no-cov tests/integration/test_amex_direct_no_cfdi_schema_postgres.py tests/unit/gastos/test_amex_direct_schema_admission.py
python -m pytest -q --no-cov tests/acceptance/test_amex_no_cfdi_browser_journey.py tests/acceptance/test_amex_no_cfdi_acceptance.py
python -m pytest -q --no-cov tests/unit/private_plugin/test_inventory.py::InventoryTest::test_inventory_is_reproducible_and_no_parse_gaps
```

Inventario regenerado por AST: 606 rutas, 574 montajes estáticos, 44 acciones, ningún parse gap; no concede autoridad nueva. Fuentes congeladas al regenerarlo; sin adaptadores habilitados ni nuevas concesiones.

## Fallos conocidos, desviaciones y riesgos

Aceptación independiente aprobada sobre guard final. I1 corregido y cerrado independientemente, con cero efectos contables en los intentos rechazados. No quedan fallos nuevos ni desviaciones funcionales pendientes dentro del alcance. Se conservan fallos de harness en sus logs; no se presentan como aprobación. No se ha solicitado ni ejecutado commit, push, PR, merge, migración o despliegue para esta entrega.

La migración será necesaria antes de activar el código. Una vez existan decisiones o cortes Sin CFDI, el downgrade de código anterior no es seguro; el rollback de esquema no borra evidencia. UAT contable real sigue separada de estas pruebas sintéticas. La corrección semántica de los mappings del catálogo requiere esa revisión de Contabilidad; no se inventó un enum de tipos de cuenta. Las pruebas de concurrencia son acotadas, no una demostración de todas las intercalaciones posibles.

## Objeto de revisión final

Diff completo: `/tmp/samchat-amex-no-cfdi-final-review-20261021.diff`. Manifiesto de SHA256 y árbol staged: `/tmp/samchat-amex-no-cfdi-final-review-20261021.json`. Ningún commit o efecto externo ejecutado.

## Archivos modificados

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261021_amex_direct_no_cfdi.sql`
- `database/migrations/20261021_amex_direct_no_cfdi_rollback.sql`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-no-cfdi-20261021-acceptance-report.md`
- `docs/roadmap/amex-no-cfdi-20261021-build-report.md`
- `docs/roadmap/amex-no-cfdi-20261021-spec.md`
- `docs/roadmap/amex-no-cfdi-20261021-story.md`
- `docs/roadmap/amex-no-cfdi-20261021-validation-report.md`
- `docs/roadmap/samchat-convergence-register.md`
- `src/devnous/gastos/amex_direct_models.py`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_direct_routes.py`
- `src/devnous/gastos/services/amex_cut_export_service.py`
- `src/devnous/gastos/services/amex_direct_accounting_service.py`
- `src/devnous/gastos/services/amex_direct_budget_service.py`
- `src/devnous/gastos/services/amex_direct_common.py`
- `src/devnous/gastos/services/amex_direct_context_service.py`
- `src/devnous/gastos/services/amex_direct_matching_service.py`
- `src/devnous/gastos/services/amex_direct_no_cfdi_service.py`
- `src/devnous/gastos/services/amex_direct_recovery_service.py`
- `src/devnous/gastos/services/amex_direct_schema_contract.py`
- `src/devnous/gastos/services/amex_direct_schema_service.py`
- `src/devnous/gastos/services/amex_direct_suggestion_service.py`
- `tests/acceptance/test_amex_no_cfdi_acceptance.py`
- `tests/acceptance/test_amex_no_cfdi_browser_journey.py`
- `tests/acceptance/test_amex_sat_budget_direct_postgres.py`
- `tests/integration/test_amex_direct_no_cfdi_backend_postgres.py`
- `tests/integration/test_amex_direct_no_cfdi_schema_postgres.py`
- `tests/integration/test_amex_direct_schema_postgres.py`
- `tests/integration/test_amex_settlement_postgres.py`
- `tests/unit/gastos/test_amex_direct_no_cfdi_budget.py`
- `tests/unit/gastos/test_amex_direct_no_cfdi_service.py`
- `tests/unit/gastos/test_amex_direct_routes.py`
- `tests/unit/gastos/test_amex_direct_schema_admission.py`
