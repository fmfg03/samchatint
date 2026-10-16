# Validación independiente del assessment AMEX

Fecha: 2026-10-15. Documento revisado: `/root/samchat/.worktrees/amex-qa-accounting-journey-20261015/docs/roadmap/amex-accounting-journey-qa-20261015.md`.

## Resultado

CERO hallazgos pendientes. El dictamen NO APROBADO para completar el estado real está sustentado. La atribución de producción, browser local con identidad inyectada y simulación sintética está correctamente separada. La observación de trazabilidad V-01 quedó resuelta al revisar el recibo adicional empaquetado; no se encontró un overclaim de contabilización productiva.

## Relevant files / evidencia contrastada

- `/tmp/amex-qa-20261015/production-audit.json`
- `/tmp/amex-qa-20261015/browser-journey.json`
- `/tmp/amex-qa-isolated-report.json`
- `/tmp/amex-qa-journey-independent-review.md`
- `docs/evidence/amex-accounting-qa-20261015/catalogue-readiness.json` y presencia del paquete final de seis artefactos en el worktree QA.
- Fuente previamente revisada del release `a983be79e1ad8a6b5aa56affe27a945160ac8166`.

## Confirmed patterns

1. SHA `ab641fa6ccafcfc046bf213764f41a61a969d05ce022899d217aa8ab3ea3cd4c` y 10,232 bytes coinciden entre fuentes productiva y aislada; esta validación no abrió el contenido CSV.
2. La distinción 145 cargos interpretados por parser frente a 140 importados + 5 omitted + 3 créditos es correcta y está explicada. Filas 145–149 del 31 de agosto y `row_outside_declared_period` constan en `actual_csv_extended` del reporte aislado.
3. El documento no dice que ausencia de candidatos equivalga a inexistencia de facturas. Cero candidatos está contextualizado por ausencia de identidad de comercio/UUID; no se sugiere saltar esa protección.
4. El reporte enumera las seis excepciones reales, cero cortes y decisiones reales faltantes. No oculta el bloqueo irreversible por simple recarga del mismo hash/período.
5. Browser está descrito como localhost, handlers reales, lectura productiva e identidad Finance inyectada; no se presenta como login o UAT productivo. Sus 148 filas, 1,314 categorías antes/después de DG, required checkbox y ausencia de búsqueda CFDI coinciden con el JSON.
6. La póliza inicial de 116.00 y su XLSX se identifican como sintéticos, incluyendo sus dependencias; importación real del CSV no se confunde con aceptación de sus facturas o contabilización real.
7. La prioridad P1 para discrepancia preview/import y resolución manualCFDI es razonable como bloqueo del resultado del usuario. Los controles de presupuesto, SAT vigente, identidad y corte completo permanecen como controles correctos.
8. Hallazgo bandeja CP postcorte se identifica honestamente como inicialmente de fuente con verificación aislada pendiente; no se afirma haberlo observado en datos reales.
9. Pago se describe sólo como enlace conservado, no como programación o liquidación ejecutada.
10. El control con rango ampliado se declara técnico en base vacía, no confirmación del período bancario ni reparación del estado original.

## Observación V-01 resuelta

Revisado `docs/evidence/amex-accounting-qa-20261015/catalogue-readiness.json`: declara read_only=true y acredita 605 CFDI SAT recibidos, 605 con XML, 566 I/MXN, 39 conceptos activos de gasto DG, 39 con mapping y 39 líneas 2026 aprobadas. El rango [2026-08-28, 2026-10-04) concuerda con el texto hasta 2026-10-03 inclusive. Los valores respaldan exactamente el párrafo consolidado. Se verificó presencia de los seis archivos de evidencia indicados; no queda objeción documental V-01.

## Likely commands / comprobación realizada

Lectura de reporte y los tres JSON; búsqueda de artefactos citados mediante `rg --files` y `rg -n`. No modificación del informe consolidado, fuente, producción, CSV o base de datos.

## Test impact / estado previo, sustituido por revisión incremental final

El escenario completo de los 145 cargos con dependencias sintéticas está ejecutándose y el reporte promete añadirlo antes de cierre. Su falta temporal no constituye sobreafirmación: el documento mantiene el límite explícito. Al añadirlo verificar: número real de cargos y créditos, decisiones sintéticas explícitas, una póliza por estado, sumas Debe/Haber, replay, exportación y situación final de CP. Conservar el dictamen negativo del estado productivo si sus datos/decisiones no cambiaron.

## Risks

Una prueba aislada verde de todo el CSV acreditaría capacidad técnica con dependencias controladas, nunca vigencia SAT real, identidad de cada factura real o autoridad para asignar decisiones de negocio. El documento actual conserva bien esta distinción.

## Unknowns

No se ejecutó autenticación productiva ni se verificó importación en COI externo. No se revisa una reparación o implementación porque el alcance es assessment de lectura.


## Revisión incremental final — fallo de exportación multicargo

Resultado: **cero hallazgos pendientes sobre la fidelidad del assessment**. Esto no aprueba el módulo: mantiene sus ocho hallazgos QA y el dictamen negativo. La simulación completa pendiente terminó en fallo y el reporte actualizado lo incorpora sin ocultarlo.

Evidencia adicional contrastada:

- `/tmp/amex-qa-isolated-full-final-receipt.json`.
- `/tmp/amex-qa-isolated-two-charge-red.log`.
- Claves `two_charge_export_minimal` y `two_charge_export_errors` de `/tmp/amex-qa-isolated-report.json`.
- Texto consolidado actualizado AMEX-QA-08, resultados de pytest y estado de CP.
- Fuente exacta desplegada `amex_cut_export_service.py`, `_cut_expense_cfdis`; `amex_direct_accounting_service.py`, `_prepare`, `confirm_direct_accounting` y `export_direct_cut`.

**Causalidad de exportación confirmada por fuente y reproducción:** `_prepare` acumula líneas de gasto/fiscales de los consumos y después agrega las contrapartidas de tarjeta en un segundo recorrido. Al confirmar, `partidas[].journal_lines` se obtiene filtrando ese journal global por expense_id, con lo que concatenar los grupos cambia su orden. El lector del corte compara primero listas completas y después listas ordenadas de identidad `(account_id,account_code,debe,haber)`; exige igualdad posicional. Por eso rechaza un desglose que conserva las mismas cuentas/importes/multiplicidades. `export_direct_cut` llama a este mismo lector antes de escoger el generador: explica el fallo compartido XLSX, CSV y ZIP sin atribuirlo a cada renderer.

El caso mínimo acredita dos gastos sintéticos, póliza única de 348.00 balanceada, seis líneas, multiconjunto igual y orden global DDDDHH frente a DDHDDH. Los tres formatos arrojan ValueError «El corte no contiene un desglose completo de su póliza»; pytest falla en assert de errores vacíos y no muestra error de teardown. Duración 4.75 s coincide. No se ejecutaron estos tests otra vez durante esta validación; se contrastaron log y receipts existentes.

El caso completo declara SHA original intacto, período ampliado sólo como control aislado, 145 cargos/consumos/asignaciones, tres créditos excluidos y una póliza balanceada. Conserva 290 líneas globales y 290 concatenadas, multiconjunto igual y orden distinto; XLSX no creado. No se convierte este resultado en contabilización o aceptación real. La distribución 80/65 de vínculos se presenta únicamente como sintética.

El informe atribuye separadamente el deadlock de teardown del escenario completo a observador y DROP SCHEMA del arnés; la reproducción pequeña limpia confirma que dicho deadlock no explica el ValueError anterior. No se afirma suite completamente verde: constan 3 iniciales aprobadas, full 1 failed + 1 teardown error, mini 1 failed.

La permanencia en CP de los 145 recognized/assigned ahora figura como observada en aislamiento y coincide con la condición sin filtro del reader previamente inspeccionado. El informe no confunde esos registros con pendientes productivos.

**Límite de la conclusión:** igualdad del multiconjunto de cuenta/importe prueba que este rechazo no deriva de pérdida de tales movimientos en los escenarios examinados. No demuestra por sí sola integridad universal de metadatos/atribución fiscal ni autoriza aflojar guards o modificar snapshots congelados. El texto consolidado mantiene el alcance concreto y exige cobertura completa para cerrar AMEX-QA-08.

Sin cambios en aplicación, fuentes financieras o producción. El eventual repro durable y el paquete final son evidencia de diagnóstico, no una corrección implementada.
