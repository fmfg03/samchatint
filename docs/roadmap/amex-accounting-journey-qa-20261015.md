# QA del recorrido AMEX: del estado de cuenta a la póliza

## Seguimiento: corrección aprobada y aceptación aislada

2026-10-15: el alcance correctivo aprobado ya pasó 797 pruebas unitarias AMEX, 404 de PostgreSQL, 9 de inventario y los recorridos reales de navegador de Contabilidad/CP y recuperación. El CSV original íntegro de 148 filas pasó recuperación de 5 cargos conservando 140 anclas originales y todos los recibos: 145 cargos reconocidos, una póliza balanceada, XLSX/CSV/ZIP completos, replay sin doble póliza y freeze verificado. Informe: [build report](amex-qa-remediation-20261015-build-report.md); [aceptación independiente](amex-qa-remediation-20261015-acceptance-report.md). Revisión final independiente: sin hallazgos críticos, importantes ni menores; resultado `CLEAN_WITH_NOTES` por los límites productivos y deuda técnica previa documentados.

Las evidencias fiscales/presupuestales positivas son **sintéticas**; esto no acredita correspondencias fiscales reales, decisiones de Juan Pablo ni UAT autenticada. Producción no se migró, recuperó ni desplegó para esta corrección. La evaluación inicial y sus fallos reproducidos se conservan a continuación; sus artefactos originales permanecen idénticos por SHA-256. Los recibos correctivos están separados en `docs/evidence/amex-qa-remediation-20261015/`.


Fecha: 2026-10-15. Release evaluado: `a983be79e1ad8a6b5aa56affe27a945160ac8166` (PR #489). Evaluación solicitada por Francisco para ejecutar el trabajo de Contabilidad con `activity.csv`, sin confundir pruebas de componentes con un resultado operativo terminado.

**Dictamen: NO APROBADO para completar el estado real actualmente cargado.** La navegación al módulo directo funciona. El archivo real reproduce bloqueos operativos concretos. La simulación completa de sus 148 movimientos, con dependencias fiscales/presupuestales sintéticas, genera una póliza balanceada para los 145 cargos pero **falla al descargar el Excel COI**. La prueba pequeña de un solo cargo sí exporta; no acredita el caso real ni el resultado a escala.

## Alcance y método

- Runtime: `samchat-gastos.service`, `copa_telmex_dashboard.py`, módulo directo de `src/devnous/gastos/`; propietario de datos: PostgreSQL y servicios canónicos AMEX/presupuesto/contabilidad.
- Estado real: `ec3f1c01-8daf-47f8-b9f9-2c7d5aad2976`, tarjeta 5007, período declarado 2026-09-01 a 2026-09-30. Es distinto del estado legacy de 28 cargos mencionado antes.
- Original: `activity.csv`, 10,232 bytes; SHA-256 `ab641fa6ccafcfc046bf213764f41a61a969d05ce022899d217aa8ab3ea3cd4c`. Coincide con el archivo privado persistido. No se editó, dividió ni cambió su hash.
- Producción: lecturas en transacción `READ ONLY`; sin consultar SAT, clasificar, asignar, excluir abonos, generar pólizas ni alterar datos reales.
- Navegador Chromium: formularios y handlers reales sobre datos productivos de lectura, servidos sólo en localhost; identidad Finance inyectada en el arnés local, no login productivo. Middleware bloquea las mutaciones; únicamente se permite POST de previsualización pura. Esto prueba la UI y sus controles HTML, no UAT autenticado en producción.
- PostgreSQL desechable: migraciones owner reales, importación del CSV intacto y servicios de negocio reales. CFDI, evidencia SAT, decisiones y catálogos de los escenarios positivos se declaran sintéticos; sólo se sustituye el transporte externo SAT.
- Revisión independiente del recorrido por un segundo agente; simulación contable por un agente verificador distinto.

## Recorrido del operador: resultado observado

| Paso | Acción del usuario | Resultado | Evidencia/límite |
|---|---|---|---|
| 1 | Abrir estados directos y continuar 5007 | PASA | Chromium llega al UUID directo correcto y muestra 148 filas |
| 2 | Seleccionar el original, tarjeta, titular persistido y septiembre; previsualizar | FALLA funcionalmente | Vista previa muestra 145 cargos y 3 créditos, sin advertir cinco cargos fuera del período declarado |
| 3 | Cargar el mismo archivo en septiembre | BLOQUEO REPRODUCIDO | Importador conserva 148 filas, crea 140 cargos y deja 5 filas omitidas, más 3 créditos |
| 4 | Conciliar con CFDI SAT descargados | PENDIENTE REAL | Los 140 cargos importados tienen cero candidatos elegibles: no tienen UUID fiscal declarado ni identidad de comercio confirmada; no significa que falten todas sus facturas |
| 5 | Resolver CFDI que no se concilian automáticamente | FALLA del recorrido | Se solicita el UUID interno de registro, sin buscador ni lista contextual; no es el UUID fiscal del XML |
| 6 | Clasificar Dirección General | FALLA UX | El selector «Torneo» contiene Dirección General y deja 1,314 categorías globales, sin filtrar sus 39 categorías autorizadas |
| 7 | Guardar clasificación | FALLA UX | El navegador exige marcar «Confirmo enviar ... a Control Presupuestal» aunque Guardar sólo clasifica |
| 8 | Juan Pablo asigna partida | NO EJECUTADO EN PRODUCCIÓN | Los cargos reales siguen sin clasificación ni envío; la asignación y el mapping se verifican en la simulación aislada |
| 9 | Confirmar estado completo y generar póliza | BLOQUEADO REAL | Además de las decisiones faltantes, `statement_row_unresolved` impide terminar por las cinco filas omitidas |
| 10 | Descargar Excel COI | FALLA en simulación completa | Tras crear una póliza balanceada de los 145 cargos en aislamiento, exportar lanza «El corte no contiene un desglose completo de su póliza». En producción no existe corte |
| 11 | Programar pago de tarjeta | ENLACE CONSERVADO | Remite al propietario existente de pagos; no se programó ni ejecutó un pago |

## Situación real, sin inventar conciliaciones

El parser reconoce **145 cargos y 3 créditos**. Cinco cargos están fechados el **31 de agosto** (filas 145–149). El período declarado comienza el 1 de septiembre: el importador los convierte a `skipped` por `row_outside_declared_period`.

El estado productivo tiene **140 cargos importados, 3 créditos y 5 filas omitidas**; todos los ítems siguen pendientes. Hay cero cargos con CFDI vinculado, proyecto seleccionado o partida asignada, y no existe corte contable.

El preview contable devuelve estos bloqueos: `merchant_identity_or_fresh_sat_evidence_pending`, `budget_assignment_required`, `budget_account_mapping_required`, `budget_assignment_changed`, `statement_row_unresolved` y `credit_exclusion_required`. La UI sólo muestra el primer motivo, por lo que oculta la dimensión del trabajo pendiente.

La lectura adicional encuentra 605 CFDI SAT recibidos para Plataforma Sports entre 2026-08-28 y 2026-10-03 inclusive, todos con XML; 566 son tipo I/MXN. Este inventario **no acredita** correspondencia, vigencia ni disponibilidad para cada cargo. Dirección General sí tiene 39 conceptos activos de gasto con cuenta y 39 líneas de presupuesto 2026 aprobado; el problema observado en su selector no es ausencia de ese catálogo.

## Hallazgos priorizados y criterio de cierre

| ID | Prioridad | Problema | Criterio de cierre verificable |
|---|---|---|---|
| AMEX-QA-01 | P1 | Preview y carga difieren en el tratamiento de cinco cargos fuera del período; el estado queda bloqueado sin camino de resolución | Preview muestra fechas/cobertura y el mismo resultado que persistirá; antes de cargar se resuelve la discrepancia. Para el estado existente, operación auditada aprobada que conserve original y anclas, sin borrar ni duplicar cargos |
| AMEX-QA-02 | P1 | Resolver una factura exige conocer su PK interno; la conciliación automática no avanza sin identidad y la salida manual no es operable | Buscar/elegir CFDI SAT desde el cargo por datos de negocio, mostrando UUID fiscal, emisor, total, fecha, vigencia y conflictos; confirmar correspondencia sin exponer un requisito técnico al operador |
| AMEX-QA-08 | P1 | Se contabiliza el estado de 145 cargos en aislamiento, pero se rechaza exportar su desglose a COI: el validador exige idéntico orden de dos representaciones que el propio owner construye en órdenes distintos | Reproducción pequeña y del CSV completo generan y exportan una póliza balanceada, con cobertura exacta de todos sus movimientos/CFDI, sin recomputar ni alterar cortes congelados |
| AMEX-QA-03 | P2 | Dirección General etiquetada como torneo; categorías de todos los proyectos mezcladas | Un selector claro de proyecto/torneo; seleccionar Dirección General muestra sólo sus categorías; backend sigue comprobando pertenencia y permisos |
| AMEX-QA-04 | P2 | Guardar clasificación obliga a confirmar un envío que no sucede | Guardar y Enviar tienen validaciones y confirmaciones acordes con su transición real |
| AMEX-QA-05 | P2 | Pantalla oculta bloqueos posteriores y no presenta el journal completo antes de confirmar | Bloqueos completos con responsable y acción; propuesta Debe/Haber, impuestos, contrapartida y totales revisable antes de confirmación |
| AMEX-QA-06 | P2 | Reader CP incluye `assigned` ya reconocidos y la pantalla ofrece acciones que el owner congelado rechaza | Cargo contabilizado deja de ser pendiente accionable; historia y evidencia siguen disponibles. Confirmado en aislamiento: 145 reconocidos permanecen en CP y reasignación rechaza `direct_cut_immutable` |
| AMEX-QA-07 | P2 | La suite browser previa no recorre el resultado contable completo | Prueba de navegador conectada a owners reales: carga → CFDI → clasificación → actor CP distinto → preview → confirmación → descarga y revisión del archivo |

P1 = impide completar el recorrido. P2 = fricción o cierre incorrecto sin prueba de duplicación financiera. No se atribuye corrupción contable: los guards bloquean la confirmación incompleta.

## Reproducciones y controles

1. **Archivo intacto + septiembre:** importación y replay idempotente; 140 cargos/3 créditos/5 omitidas. Excluir los tres créditos explícitamente en aislamiento elimina ese motivo, pero no `statement_row_unresolved`. Confirmación rechazada, cero pólizas.
2. **Control de período en base vacía:** mismo archivo intacto y rango 2026-08-31 a 2026-09-30 produce 145 cargos/3 créditos/0 omitidas. Es un control técnico; no declara cuál fue el período bancario real ni corrige producción.
3. **Reintento con otro período:** el mismo hash ya registrado devuelve `file_context_conflict`; recargar el CSV no es reparación. No cambiar bytes para evadir el guard.
4. **Chromium y datos reales de lectura:** 148 filas; 1,314 opciones de categoría antes y después de seleccionar Dirección General; ninguna relación de proyecto conservada en las opciones HTML. `checkValidity()` de Guardar es false sin confirmar envío y true al marcarlo. Sin errores JavaScript ni overflow de página en 1440 y 390 px; documento de escritorio de 26,834 px de alto. Que el diseño no desborde no acredita usabilidad con 148 movimientos.
5. **Prueba positiva sintética inicial:** un cargo de 116.00 recorre Finance → CP → confirmación y genera una póliza: gasto 100.00 + IVA 16.00 = pasivo de tarjeta 116.00; Excel COI real, RFC sin espacios, replay sin doble póliza y exportación congelada ante cambios posteriores de catálogo.
6. **CSV completo con dependencias sintéticas:** archivo original intacto, rango del control entre meses, 145 CFDI de prueba ingeridos, identidad de comercio y transporte SAT sintéticos, tres abonos excluidos explícitamente, clasificación Finance y 145 asignaciones por actor CP distinto. Generación de una póliza balanceada, 145 consumos reconocidos y replay del mismo corte aprobados. **Exportación XLSX fallida** en `amex_cut_export_service.py:45`; no existe un Excel completo descargado. Los dos desgloses conservan 290 líneas y el mismo multiconjunto de cuenta/importe, pero en distinto orden. La distribución 80 vínculos automáticos/65 manuales pertenece exclusivamente a las facturas sintéticas; no mide la tasa real de conciliación.
7. **Reproducción mínima del defecto de exportación:** dos cargos sintéticos de 116.00 y 232.00, una póliza balanceada de 348.00, seis líneas en ambos desgloses. El global tiene orden `D,D,D,D,H,H`; agrupado por gasto tiene `D,D,H,D,D,H`. Mismas cuentas/importes/multiplicidad y distinto orden. XLSX, CSV y ZIP devuelven el mismo `ValueError`. Prueba roja reproducida en **4.75 segundos**, con teardown limpio. Confirma un defecto de orden, no pérdida de movimientos ni redondeo en este caso.

Resultados de pytest en invocaciones separadas: **3 pruebas iniciales aprobadas** en 11.18 s; prueba completa **1 fallida + 1 error de teardown** en 288.71 s; reproducción mínima **1 fallida** en 4.75 s. Los dos fallos funcionales son del exportador desplegado. El error de teardown fue un deadlock entre el observador aislado y `DROP SCHEMA`, ajeno a la exportación; no se usa como evidencia de defecto productivo. No hay una suite de aceptación completamente verde.

## Secuencia propuesta para estabilizar el módulo

1. Resolver la discrepancia de período antes de persistir y diseñar la recuperación auditada del estado ya cargado. No es una migración legacy: este estado ya es directo. Su recuperación debe especificar ownership e inmutabilidad; no se autoriza aquí una actualización improvisada de flags/fechas.
2. Hacer operable la mesa de Contabilidad: CFDI localizable por datos de negocio, identidad comercial explicada, proyecto y categorías dependientes, separación clara Guardar/Enviar y todos los bloqueos visibles.
3. Corregir la exportación multicargo antes de certificar el cierre. Cerrar la transferencia a Juan Pablo y el regreso a Contabilidad: pendientes verdaderos, cuentas desde la partida, preview contable completo y descarga desde corte congelado. Conservar el acceso a pago de tarjeta.
4. Repetir el recorrido completo en navegador con todos los movimientos, identidades separadas y datos de prueba identificados; después completar decisiones fiscales/presupuestales reales con sus responsables y ejecutar UAT contable.

La QA está autorizada y ejecutada. Cambiar comportamiento de captura o reparar el estado productivo requiere especificación y aprobación del alcance afectado según `AGENTS.md`; esta evaluación no modifica ni elimina protecciones fiscales, doble uso de CFDI, exclusión entre fuentes, control presupuestal o corte completo.

## Comandos y artefactos

Desde el release exacto:

```sh
PYTHONPATH=src:. /srv/samchat/venvs/release-598e7a286/bin/python /tmp/amex-qa-production-audit.py
PYTHONPATH=src:.:/tmp /root/samchat/.venv/bin/python /tmp/amex-qa-browser-journey.py
PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python -m pytest -q -o addopts='' -o asyncio_mode=auto /tmp/amex-qa-isolated-assessment.py
```

Para repetir sólo el defecto pequeño con PostgreSQL aislado configurado, usar `-k two_charge_coi_export` en la última invocación. No requiere el CSV real ni credenciales productivas. La evidencia durable conserva también el caso mínimo en `docs/evidence/amex-accounting-qa-20261015/reproduce_multicharge_export.py`, ejecutable explícitamente con pytest.

La variable de conexión del último comando apunta exclusivamente al PostgreSQL desechable. Las lecturas productivas usan internamente la conexión activa sin imprimir credenciales. No se guardan CSV, XML, credenciales ni capturas con datos de negocio en Git.

Evidencia estructurada junto al reporte, en `docs/evidence/amex-accounting-qa-20261015/`. Capturas privadas y arneses locales: `/tmp/amex-qa-20261015/`, `/tmp/amex-qa-isolated-*`. Excel sintético inicial: `/tmp/amex-qa-isolated-SYNTHETIC-poliza-coi.xlsx`.

## Cierre de evaluación

- Archivos cambiados: sólo este reporte y evidencia QA de metadatos; fuente de aplicación, migraciones y producción sin cambios.
- Pruebas: auditoría real de lectura, recorrido Chromium y escenarios PostgreSQL aislados; los tests que verifican un bloqueo esperado pasan aunque la aceptación del módulo sea negativa.
- Fallos conocidos: los hallazgos anteriores son del release desplegado, no causados por esta evaluación. La ausencia de CFDI/asignaciones reales permanece explícita. El deadlock del teardown completo pertenece a infraestructura del arnés; el defecto de exportación vuelve a reproducirse con teardown limpio en el caso pequeño. Se corrigió en el arnés de navegador el selector de conteo de filas (`tr` creado directamente sin `tbody`); la ejecución final exige 148 filas y no cambió la aplicación.
- Límites: no login productivo automatizado, consultas SAT reales, decisiones reales de Juan Pablo ni importación en COI externo. No se afirma póliza productiva ni aceptación de Finanzas.
- Desviaciones: ninguna implementación ni reparación añadida a la QA. La simulación completa del CSV se detuvo por un defecto de exportación comprobado después de la generación del corte aislado; la póliza sintética pequeña no sustituye ese resultado.
- **Canon unchanged:** se evalúa el cumplimiento del recorrido ya aprobado; no se modifica producto, arquitectura ni autoridad. Una corrección futura evaluará su impacto y la necesidad de revisión humana del canon.
