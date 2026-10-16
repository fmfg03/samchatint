# AMEX directo: aceptación independiente

Fecha: 2026-10-15. Resultado: **32 pruebas PostgreSQL de aceptación pasan**, 57,29 s. Las regresiones existentes de RFC XLSX, cortes y programación AMEX pasan: **46 pruebas**, 3,63 s. No quedan fallos en esta lane. La cobertura de la especificación es parcial: los límites explícitos se registran abajo; no se afirma aceptación completa ni UAT.

Historia/especificación de 2026-10-14 aprobadas por el usuario. Baseline `d8c9fda23`, worktree `amex-sat-budget-direct-20261015`. Sin cambios productivos, red SAT real, pagos reales, commit, push ni despliegue.

## Alcance y entorno

Archivo propio: `tests/acceptance/test_amex_sat_budget_direct_postgres.py`. Implementación, tests de builders y canon no se modificaron en esta lane.

Canons leídos en orden y hashes iniciales verificados contra register antes del build: Producto `b55ae07a926eb8de78e07345b744e59b6fdf30021e17208e12309580aa32875c`, Ingeniería `0d2128b70f40c5670b2153599dd0ab4cb2b768ebba634ce5a9b6283baa9ecd81`, Sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`. Revalidación independiente posterior: los tres archivos actuales coinciden con register; Producto `7062756077f455741f03027b335f0be6627476c23f9f0b4ab8744f0a9608c99e`, Ingeniería `bd011d2d33deaea655e4bc3f863e61610454520544d477e1cba1560f6fabd47c`, Sweep sin cambios. Las enmiendas aprobadas pertenecen al owner canon.

PostgreSQL efímero 16, host local puerto55471. Fixture exige localhost/127.0.0.1, `test_user`, `devnous_test`; crea schema UUID y elimina exclusivamente ese schema al terminar. Metadata instala tablas legacy sin FKs de dominios ajenos. Los owners AMEX se excluyen de create_all y se instalan mediante SQL real: shared recognition20261001, approved-informe20261013, statement20261013 y direct20261014. `ensure_budget_schema` se usa sólo en preparación explícita del schema de prueba. Roles, UUIDs, scopes, XML y receptores son sintéticos. Las consultas de vigencia sustituyen únicamente transporte SOAP; el resto de APIs, transacciones, permisos, guardas SQL, fiscalidad, readers y renderizadores son reales.

## Matriz de aceptación

| Criterio aprobado | Método y evidencia | Resultado | Notas o límites |
| --- | --- | --- | --- |
| Estado/cargo directo sin INFORME | CSV→intake→alias/status→matching→clasificación→CP→corte; SQL Documento=0, Expense=1, journal=1 | PASS | No UAT autenticada |
| Original íntegro y significado del signo | Cuatro perfiles/signos; BYTEA/hash/tamaño exactos; replay; all-credit/all-unknown; descarga con autorización recargada | PASS | No se afirma prueba independiente del límite10MiB |
| Matching ordinario sin UUID bancario | Alias exacto confirmado, CFDI ingresado por owner SAT, receipt observado; UUID source ausente y cfdi/status/alias pointers reales | PASS | SAT stub identificado como sintético |
| Ambigüedad y falta de evidencia | Sin alias, cancelado y un CFDI para dos cargos permanecen pendientes | PASS parcial | No todas las variantes manual/shared/PASE/propina en esta lane |
| Vigencia fresca y fuente exacta | Receipt real: límite24h inclusive, +1µs stale; XML cambiado invalida receipt; SOAP con NOWAIT de import row demuestra ausencia de lock financiero; revocación de actor durante SOAP impide matching | PASS parcial | Chunks50/concurrencia2/timeout30s y continuación no probados independientemente aquí |
| Autoridad real, sin grant por nombre | `access_control_rules` real/_load_rule real; actor inactivo, mismo nombre Juan Pablo con rol incorrecto y denegación persistida bloquean intake antes de efectos | PASS parcial | No todos los permisos HTTP/middleware por rol |
| Contabilidad proyecto/categoría; CP sólo partida | Clasificación UUID explícita; CP UUID configurado y scoped selecciona BudgetLine approved; mapping gasto y pasivo tarjeta verificados | PASS parcial | Negativos de cada versión/línea y edición de plan frozen corresponden a pruebas builder/root |
| Contextos append-only e invalidación | SQL UPDATE/DELETE de contexto/original rechaza; revisión2 conserva fila1, libera vínculo fiscal propio, limpia pointers/mapping; historial assignment/event se conserva; reclasificación misma categoría y reader no reutilizan partida vieja | PASS | No borrado de historia; no regularización retrospectiva |
| Presupuesto sin duplicación | Reader `build_budget_actuals_snapshot` real: una proyección antes, una línea ledger después; base fiscal100 frente a cargo116; no suma proyección+journal; stale assignment no reaparece | PASS | No dataset productivo |
| Corte íntegro, fecha explícita | Fuente unresolved bloquea todo aunque debit esté matched/assigned; all-credit excluido no genera journal cero; Sep29+Oct1 bajo estadoSep29-Oct29 genera una pólizaOct15, fechas originales intactas; Oct cerrado bloquea fecha contable aunque cargoSept esté abierto | PASS | Caso mixto debit116/crédito20 congela amount=None, bank_signed_amount20 y cero líneas de journal para el crédito; no aplicación contable productiva |
| Idempotencia, atomicidad y congelación | Dos confirmaciones concurrentes producen mismo corte/journal; fallo SQL después de journal flush revierte journal/lines/consumptions/cut y fuente intacta; fallo de intake después de source revierte archivo/anchors; updates/deletes de cut/state/items/membership bloqueados | PASS parcial | Cruces completos direct↔legacy/report/bridge/pre-recognized y los14 históricos no ejecutados en esta lane |
| Export COI/RFC | XLSX real del corte directo con cuentas/fiscalidad; RFCs limpios y export frozen ante cambio catálogo; regresiones existentes RFC padding/CSV/cuts incluidas | PASS | Adapter recibe AmexDirectAccountingCut real, grupo amex-direct-cut, descripción Estado y sin informe_id ficticio; no importación real en COI |
| Pago preservado | Owner real crea SOLICITUD aprobada/programada, no pagada, sin journal; con o sin matching/corte previo; owner de posting existente debita pasivo80 y acredita banco80 sin nuevo Expense | PASS parcial | Invocación sintética del owner de posting; no prueba bancaria ni confirmación real Payment Run |
| Schema fuera del runtime | Nueve tablas nuevas fuera de runtime_managed_tables; observer SQL de clasificación/asignación/preview/confirm/export registra cero CREATE/ALTER/DROP/TRUNCATE | PASS | Intake/ingestión de fixtures separados del tramo observado; ownerSQL adicional tiene su propia lane |
| UI y roles en navegador | Tests de rutas builders y validación independiente deben aportar evidencia propia | PENDIENTE | No navegador autenticado ni aceptación de negocio ejecutados aquí |

## Comandos y resultados

```bash
TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user:amex_test_only@127.0.0.1:55471/devnous_test \
PYTHONPATH=src /root/samchat/.venv/bin/python -m pytest \
tests/acceptance/test_amex_sat_budget_direct_postgres.py -q --no-cov --tb=short
```

**32 passed**, 57,29 s. Log local: `/tmp/amex-direct-acceptance-32-final.log`. Las 814 advertencias corresponden a SQLAlchemy/deprecaciones utcnow y al ciclo legacy Documento/Expense de metadata; no son fallos ocultos.

```bash
PYTHONPATH=src /root/samchat/.venv/bin/python -m pytest \
tests/unit/gastos/test_coi_poliza_exporter_rfc.py \
tests/unit/gastos/test_amex_cut_exports.py \
tests/unit/gastos/test_amex_card_payment_service.py -q --no-cov
```

**46 passed**, 3,63 s. Black --check y Flake8 --max-line-length88 del archivo de aceptación pasan. Cobertura/tipos/gates generales los consolida root: no se acreditan por estos resultados.

## Hallazgos resueltos y límite del cierre

La aceptación detectó dos errores reales de orden ORM frente a triggers: el receipt se insertaba antes de actualizar la marca AMEX del Expense, y el event se insertaba antes de actualizar su projection/hash/version. Backend corrigió los flush correspondientes, sin relajar las guardas. El primer intento completo también encontró un comparator de identidad fiscal en construcción; su corrección pasó el flujo completo. El guard de mes propio de INFORME fue retirado exclusivamente del nuevo origen directo, con prueba cruzando septiembre/octubre. Los errores de expectativa de status y shape de reader en los tests se corrigieron contra el contrato real y no mediante bypass de implementación.

La corrección de contexto preserva assignment histórico, eventos y revisions; limpia sólo la decisión actual y exige nueva selección CP. El reader verifica pointer/identidad actual y no eleva la fila antigua a asignación vigente. La aceptación exige ese comportamiento y mantiene el conteo histórico.

Los procesos de esta lane terminaron antes de entregar el recibo final. Sin desviaciones de ownership. Lo aprobado aquí son resultados sintéticos comprobables; publicación, migración owner-run, despliegue, UAT y aceptación contable/bancaria siguen siendo hechos distintos.

## Revalidación final y cobertura independiente

Se añadieron aserciones del contrato de origen directo al exportador compartido y un caso completo de corte mixto con crédito conocido. Ambos pasan juntos: `2 passed, 30 deselected`, 6,81 s, log `/tmp/amex-direct-acceptance-new-final.log`. Los tests no fabrican INFORME, importe fiscal cero ni pago. Black, Flake8 (88) y git diff --check del archivo pasan.

La primera medición de 31 casos tuvo 31 PASS funcionales pero exit1 por el umbral global85: cobertura de estos siete servicios por aceptación sola72,39% (1358 instrucciones,375 faltantes). Es un déficit de cobertura de esta lane, no una excepción al gate del proyecto. La cobertura combinada de builders/root y changed-lines se debe evaluar separadamente antes de afirmar validación completa. La nueva medición de 32 casos usa `-o addopts='' --cov-fail-under=0` sólo para emitir el dato independiente, no para dispensar el gate85. Artefactos: `/tmp/amex-direct-acceptance-final.coverage`, `/tmp/amex-direct-acceptance-final-coverage.json`; log original con gate fallido `/tmp/amex-direct-acceptance-final.log`.

Gaps independientes exactos: límites SOAP50/concurrencia2/30s/continuación, toda la matriz de excepciones manuales/propina/PASE/shared, cruces ya-reconocidos direct↔report↔bridge y14 históricos, todos los permisos HTTP por rol, navegador y UAT real. Las pruebas SQL schema y browser de otras lanes pueden cubrir partes de esta matriz; este recibo no las cuenta como ejecuciones propias ni como evidencia de producción.

Medición final: **32 passed, 80,28 s**, exit0 con umbral individual desactivado explícitamente; **73.64%** (1358 instrucciones, 358 faltantes). Log `/tmp/amex-direct-acceptance-32-coverage-final.log`. No cumple por sí sola85%; root debe combinar y evaluar changed-lines.

## Complemento de coordinación tras el cierre independiente

2026-10-15: el cierre global registra113HTTP y3Chromium aprobados,40ownerSQL aprobados,132backend+aceptación aprobados y767unitarios de AMEX/presupuesto aprobados (conteos solapados). Los límites SAT50/2/timeout/cursor se ejercitaron en la lane backend. Validación final sin hallazgos abiertos y cobertura modificada consolidada90.65%, con artefactos versionados en los reportes `amex-sat-budget-direct-20261015-build-report.md`, `-validation.md` y `-coverage.json`. Este complemento no transforma la lane independiente en pruebas de escenarios que no ejecutó ni acredita UAT productivo.


## PR488 regression acceptance v2

Five review findings have red/green evidence and focused negative tests. Final source: 782 unit,33 PostgreSQL acceptance/budget,99 backend,14 focused PostgreSQL and4 Chromium passed; counts overlap. Coverage2110/2302=91.66%. Nine inventory tests pass. Detailed commands, failures, attribution and production boundaries are in the build report. No production migration, deployment, real SAT or authenticated UAT was performed.
