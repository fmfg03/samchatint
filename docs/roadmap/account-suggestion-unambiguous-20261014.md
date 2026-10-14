# Sugerencia de cuenta contable inequívoca

Historia: como Finanzas, quiero que una sugerencia de cargo corresponda al concepto y catálogo vigente, para que apoyos de asociaciones y arbitrajes no adopten una cuenta ajena por el orden de los registros.

Autorización de Francisco: «Si, aprobado», 2026-10-14 17:53:36 UTC, transmitida en la delegación. Alcance: preparar código, pruebas y revisión independiente; sin commit, push, PR, merge, deploy, migración, catálogo ni reclasificación real. Base inspeccionada: main `71247de778ba2c69783cd68427a7084c01e7c42f`; candidato aislado en `/tmp/samchat-account-diagnostic`.

## Contrato

- La partida presupuestal activa y su cuenta activa mantienen prioridad canónica.
- Por torneo, normalizar ambos conceptos; evaluar todas las coincidencias exactas antes de parciales. Múltiples coincidencias no se resuelven por orden, incluso si comparten destino: requieren depurar/seleccionar explícitamente el catálogo.
- Una única coincidencia requiere subcuenta y base del torneo o del propio mapeo coincidente. Nunca usar base/subcuenta del primer mapeo ajeno ni primera cuenta por prefijo. La cuenta completa debe existir en el catálogo activo.
- Una coincidencia parcial única permanece consultiva con confianza inferior al umbral determinista y requiere selección explícita. Sin coincidencia o con ambigüedad, esta regla no propone cuenta. Otros tiers consultivos pueden seguir mostrando candidatos, sin preseleccionarlos.
- La UI preselecciona cargo únicamente para reglas inequívocas de catálogo: partida, torneo exacto, tarjeta AMEX explícita y no deducible por proyecto ya configurado. Se conserva la aceptación humana por fila. Las heurísticas/históricos/LLM y AMEX legacy aprendido no reciben selección automática por confianza alta.
- No reemplazar cargo previamente persistido ni una elección manual en la página durante aceptación masiva. Abrir la página no escribe. No cambia la contabilización, fiscalidad ni exportación: siguen consumiendo la selección persistida.
- No introducir reglas de palabras «apoyo»/«arbitraje» ni cuentas/IDs de negocio. Los destinos deseados se verifican con fixtures explícitos: apoyos → Cuotas Asociaciones; arbitrajes → Arbitrajes.

Canon unchanged: se aplica la autoridad existente del catálogo y selección explícita y se elimina inferencia arbitraria; no se cambian invariantes de documentos, presupuesto, contabilidad, impuestos, exportación, permisos o AMEX. Ningún canon se edita.

## Aceptación y regresiones

Ambos destinos válidos, orden invertido, concepto ausente/vacío, mapeo ambiguo exacto/parcial, exacto posterior a parcial, cuenta exacta ausente, base solo del mapeo correspondiente, mapping incompleto, cuenta/partida inactiva, prioridad de partida, UI heurística sin preselección y cuenta manual preservada. Ejecutar también las pruebas existentes de reglas AMEX/no deducibles, Control Presupuestal, propagación y COI disponibles. Revisión independiente del diff y sus límites antes de entregar.

## Incidente e histórico

El defecto técnico está reproducido; faltan folios y catálogo vigente para atribuir el incidente real. Esta preparación no acredita despliegue ni corrección de históricos. Reclasificar requiere inventario/dry-run por folio/estado, diff de cuentas confirmado por Finanzas, respaldo/auditoría y reversión/ajuste cuando exista reconocimiento/exportación; autorización separada. AMEX fusionado no se reabre.

## Evidencia visual adicional y límite de atribución

Captura aportada por Francisco (`1000266376.jpg`, Library `libfile_654ca2b1387c81919201e567e8470221`; transcripción visual del parent). Es el catálogo presupuestal: Partida / Tipo / Proyecto / Sub Proyecto / Cuenta presupuestal / Contracuenta presupuestal. En Liga Telmex Telcel de Béisbol, Cuotas de Asociaciones muestra `5300-012-010 · CUOTAS ASOCIACIONES DE BEIS…` en diversas fases, incluida Fase Nacional. Otra fila de nombre aparentemente similar, Generales del torneo, muestra «Sin cuenta». Ceremonia de Inauguración muestra `5300-012-028 · GASTOS FASE NACIONAL LTB…`; la contracuenta visible es `2120-002-099 · ACREEDORES VARIOS`. Estos códigos son evidencia recibida, no nuevos mappings ni datos aplicados.

`resolve_budget_concept` (`samchat/budgets/service.py:2988`) selecciona por UUID explícito y valida proyecto/fase; no resuelve solo por nombre visible. Las claves de importación incluyen partida/subproyecto (`:2240`), mientras la identidad semántica además incorpora cuenta (`budgets/deduplication.py:64`). Dos filas parecidas o una sin cuenta no son prueba de duplicados idénticos. El resolver de importación (`service.py:1951`) rechaza coincidencias múltiples de una misma fase cuando no hay evidencia SSOT inequívoca. Regresiones nuevas verifican ID seleccionado sin cuenta frente a homónimo mapeado, fase/cuenta como identidad y rechazo de mapping ambiguo independientemente del orden. No se modifica lógica presupuestal en esta entrega.

La captura confirma que Fase Nacional como subproyecto y GASTOS FASE NACIONAL como cuenta son conceptos diferentes. Falta unir sus UUID/estado/auditoría a los folios del incidente; tampoco hay evidencia visual de la cuenta de Arbitrajes. No se generaliza una reclasificación de todas las partidas de una fase.

Revisión independiente identificó un consumidor preexistente adicional: `routes/webhook_handler.py:499-521` autoasigna tras sincronización CFDI por umbral de confianza sin consultar la elegibilidad de preselección. El defecto de torneo deja de aplicarse allí al cambiar el servicio compartido, pero una sugerencia histórica/reglas todavía podría asignarse. Ese webhook no se cambia dentro del alcance aprobado para sugeridor/UI; corregir su criterio y sus regresiones exige acordar expresamente esa extensión antes de afirmar cierre integral de la asignación automática.

## Resultado de preparación

- Pruebas funcionales del candidato: **170 passed** en ocho archivos de gastos, incluyendo 51 regresiones nuevas; **45 passed** en presupuesto/deduplicación con preimport del módulo gastos para evitar el ciclo de imports existente. Total **215 passed**, sin DB externa. Fixture SQL de Control Presupuestal usa SQLite en memoria. Node 24 ejecuta el colector real de aceptación masiva.
- Baseline rojo: siete regresiones de concepto ausente/cuenta exacta ausente fallan contra el source original y pasan contra el candidato. Log `account-baseline-red.log`.
- Cobertura: **26/26 sentencias añadidas/modificadas ejecutables cubiertas** (25 sugeridor, una condición Python de UI; HTML/JS probado por render y Node). No es cobertura global. La corrida instrumentada falla en una prueba SQLite heredada por `TypeError: 'InternalTraversal' object is not callable` en SQLAlchemy Cython; revisión independiente reprodujo el mismo fallo en baseline (163 passed/1 failed), mientras candidato sin cobertura pasa. No se modifica esa deuda ni se afirma suite global verde.
- Compilación de ambos módulos correcta; Black check de los dos archivos nuevos de tests correcto. Se formatearon únicamente tramos editados del servicio para conservar diff acotado; no se afirma lint/mypy global.
- Revisión independiente: sin hallazgos funcionales pendientes del diff; leyenda UI aclarada. Confirmó pruebas por ID y protección de cambios manuales en JS. Revisó el límite de webhook descrito arriba. Canon SHA-256 intacto. Checkout original sigue limpio.

Comandos reproducibles (desde candidato, Python 3.12 y entorno aislado `/tmp/samchat-account-venv`):

```sh
env -u DATABASE_URL -u POSTGRESQL_URL EXPENSE_REPORT_RECEIPT_BLOB_COLUMN=archivo_data PYTHONPATH=src /tmp/samchat-account-venv/bin/python -m pytest -o addopts='' tests/unit/gastos/test_cuenta_contable_suggester_mapping.py tests/unit/gastos/test_account_suggestion_preselection_ui.py tests/unit/gastos/test_amex_catalog_suggester.py tests/unit/gastos/test_no_deducible_project_rules.py tests/unit/gastos/test_finance_budget_tax_regressions.py tests/unit/gastos/test_budget_control_gate.py tests/unit/gastos/test_accounting_cleanup_period_and_origin.py tests/unit/gastos/test_coi_poliza_exporter_description.py -q

env -u DATABASE_URL -u POSTGRESQL_URL EXPENSE_REPORT_RECEIPT_BLOB_COLUMN=archivo_data PYTHONPATH=src /tmp/samchat-account-venv/bin/python -c 'import devnous.gastos.services.cuenta_contable_suggester; import pytest; raise SystemExit(pytest.main(["-o", "addopts=", "tests/unit/test_budget_deduplication.py", "tests/unit/test_budget_service.py", "-q"]))'
```

Dependencias instaladas solo en venv temporal; no se cambian manifests/requirements del proyecto. Recibo de versiones en `account-candidate-pip-freeze.txt`. Logs y diff revisable se entregan en `/workspace/diagnostics`.
