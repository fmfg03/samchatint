# QA AMEX: rechazo de aceptación por exportación de múltiples cargos

Fecha2026-10-15. Release inspeccionado `/srv/samchat/releases/gastos-prod-a983be79e-pr489-amex-odilon-nav`, commit `a983be79e1ad8a6b5aa56affe27a945160ac8166`.

**Dictamen: NO ACEPTADO para completar estados de cuenta en pólizas COI exportables.** Con el CSV original y dependencias fiscales/presupuestales declaradas sintéticas se llegó a una póliza balanceada de145 cargos, pero falló la exportación. El defecto se reprodujo con sólo dos cargos: fallan XLSX,CSV yZIP. No se escribió implementación ni se modificó producción.

| Criterio de aceptación | Cobertura | Resultado | Notas o brechas |
|---|---|---|---|
| Cargar148 filas del CSV conservando fuente e idempotencia | Importer canónico y PostgreSQL aislado | PASS | SHA-256 original preservado, recarga idéntica sin duplicados |
| Terminar estado productivo declarado septiembre1–30 | CSV intacto, preview y confirmación canónicos | BLOQUEADO | Cinco cargos de agosto31 quedan omitidos por período y producen `statement_row_unresolved` |
| Tratar abonos con confirmación explícita | Exclusión canónica de3 abonos en aislamiento | PASS | No resuelve las cinco filas fuera de período |
| Importar el CSV completo con rango entre meses | Control inicial en DBvacía agosto31–septiembre30 | PASS |145 cargos,3 abonos,0 omitidas. No afirma que sea el período real ni repara el estado existente |
| Corregir período existente mediante recarga | Mismo hash, distinto período | BLOQUEADO POR CONTRATO | `file_context_conflict`; no se debe editar CSV/hash para eludirlo |
| Completar Contabilidad → CP → póliza con145 cargos | CSV intacto + dependencias explícitamente sintéticas + servicios reales | PASS SINTÉTICO hasta póliza |145 CFDI de prueba,47 alias sintéticos,80 matches automáticos,65 vínculos manuales,145 partidas asignadas por actor CP separado |
| Generar póliza balanceada e impedir duplicados | Confirmación y replay canónicos de145 cargos | PASS SINTÉTICO |1 póliza,145 consumos reconocidos,290 líneas,3 créditos excluidos; replay conserva corte |
| Exportar esa póliza completa a COI | Exportador canónico XLSX | **FAIL P1 de aplicación** | Error después de contabilizar; no existe artefacto XLSX completo exitoso |
| Exportar póliza mínima de dos cargos | Dos CFDI sintéticos, corte balanceado, tres formatos | **FAIL XLSX,CSV yZIP** | Repro determinista4.75s, sin error de teardown |
| Retirar contabilizados de la cola CP | Observador canónico posterior al corte | **FAIL de experiencia** |145 reconocidos siguen listados; intentar reasignar se rechaza `direct_cut_immutable` |
| Exportar un solo cargo | Caso sintético116.00 | PASS SINTÉTICO limitado | No cubría múltiples cargos y no acredita el estado real |
| Conciliar fiscalmente y contabilizar el estado real | No se fabricaron hechos fiscales reales | NO ACREDITADO | No se probaron CFDI/decisiones reales ni recepción externa por COI |

## Causa comprobada del fallo de exportación

La preparación acumula primero todas las líneas de gasto/IVA y después todos los abonos de tarjeta. La confirmación agrupa esas líneas por partida. El exportador vuelve a aplanar las partidas y exige la misma **secuencia** que el diario global. Con dos cargos la secuencia global es `[D,D,D,D,H,H]` y la agrupada `[D,D,H,D,D,H]`.

La prueba verifica que las seis identidades `(cuenta_contable_id,cuenta_codigo,debe,haber)` coinciden exactamente como multiconjunto, incluyendo multiplicidad; sus importes y cuentas están completos. Sólo difiere el orden. La prueba grande confirma lo mismo con290 líneas. `amex_cut_export_service.py:45` rechaza con `ValueError: El corte no contiene un desglose completo de su póliza.` antes de elegir el generador CSV/XLSX/ZIP. No se implementó una solución ni se debilitó el control del snapshot.

## Datos reales y límites de simulación

CSV `/root/samchat/activity.csv`,10,232 bytes,148 filas, SHA-256 `ab641fa6ccafcfc046bf213764f41a61a969d05ce022899d217aa8ab3ea3cd4c`, perfil explícito `debit_positive`. El parser reconoce145 cargos y3 abonos; declarar septiembre1–30 convierte cinco cargos2026-08-31, filas145–149, a `skipped/row_outside_declared_period`. Eliminar las3 exclusiones pendientes de abonos en aislamiento deja intacto el bloqueo de filas. El servicio rechaza confirmar el estado incompleto.

El control grande usa **sólo como escenario inicial aislado** agosto31–septiembre30, sin cambiar bytes. CFDI/XML, vigencia, identidades comerciales, actores, proyecto, categoría, cuentas, presupuesto y partidas son sintéticos; el tratamiento sin impuestos del control grande tampoco es una afirmación del IVA real. Los servicios internos de negocio fueron reales; sólo se sustituyó transporte externo SAT. Cero consultas SAT reales, cero credenciales productivas leídas y cero escrituras productivas. No es UAT autenticado ni aceptación de Finanzas.

## Ejecuciones y atribución de fallos

Las siguientes ejecuciones son separadas; **no existe una suite global aprobada**:

- Tres comprobaciones iniciales: `3 passed` en11.18s. Cubren fuente/idempotencia/bloqueos, control entre meses y exportación de un solo cargo.
- Escala completa145 cargos: `1 failed,1 error,3 deselected` en288.71s. Fallo de aplicación: exportación. Error adicional: deadlock durante `DROP SCHEMA` del fixture frente al observador concurrente de QA, posterior y separado del fallo de exportación.
- Repro mínimo final: `1 failed,4 deselected` en4.75s, sin error de teardown. XLSX,CSV yZIP fallan con el mismo mensaje.
- Primer ensayo del repro mínimo: fecha predeterminada de fixture fuera del período; se corrigió sólo la fecha a2026-10-15. Ensayo conservado y atribuido a preparación de prueba, no al bug de exportación.
- Advertencias: deprecaciones existentes SQLAlchemy/datetime, sin ocultarlas ni ampliar alcance para corregirlas.

Duración288.71s incluye fixtures, XML sintéticos, alias, lecturas, conciliación, asignaciones, exportación y contención de teardown. Los145 eventos CP abarcan10.70s entre primer y último evento. Los comienzos de lote SAT están separados48.93s y74.47s, pero esos intervalos incluyen lecturas de vista y trabajo local; **no miden exclusivamente cada petición ni latencia SAT productiva**. No se atribuye un SLA productivo incumplido que no se midió.

## Reproducción y evidencia

Desde la raíz del repo, con `TEST_AMEX_DATABASE_URL` configurada al PostgreSQL desechable admitido por la fixture (localhost:55471,test_user,devnous_test):

```sh
PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps .venv/bin/python -m pytest -q -o addopts='' -o asyncio_mode=auto /tmp/amex-qa-isolated-minimal-reproduction.py::test_two_charge_coi_export_must_succeed_after_balanced_cut
```

El reproductor durable usa `AMEX_QA_REPO_ROOT` o el directorio actual, sin credenciales incrustadas. Su caso es idéntico al repro final ejecutado; sólo se extrajeron imports/fixture y destino JSON. No se repitió otra prueba larga.

- `/tmp/amex-qa-isolated-minimal-reproduction.py`: repro mínimo portátil, deliberadamente rojo.
- `/tmp/amex-qa-isolated-two-charge-red.log`: resultado mínimo final.
- `/tmp/amex-qa-isolated-full-run.log`: resultado grande original, error de exportación y teardown separado.
- `/tmp/amex-qa-isolated-full-final-receipt.json`:145 cargos reconocidos,290 líneas, comparación de orden/multiconjunto y eventos.
- `/tmp/amex-qa-isolated-full-observer.json`: cola CP145 después de póliza y rechazo de reasignación.
- `/tmp/amex-qa-isolated-full-timings.json`: comienzos de lotes, con límites indicados.
- `/tmp/amex-qa-isolated-report.json`: evidencia integrada de todos los casos.
- `/tmp/amex-qa-isolated-assessment.py`: harness completo, incluye CSV real y casos sintéticos.
- `/tmp/amex-qa-isolated-run.log`: tres casos iniciales verdes.
- `/tmp/amex-qa-isolated-SYNTHETIC-poliza-coi.xlsx`: sólo póliza sintética de un cargo116.00; nunca importar a producción.
- **No existe** `/tmp/amex-qa-isolated-FULL-CSV-SYNTHETIC-poliza-coi.xlsx`: la exportación grande falló.

Contenedor propio detenido y eliminado, observador finalizado y esquemas aislados eliminados junto al contenedor. Sin cambios de fuente o repositorio; archivos creados exclusivamente en `/tmp`. Canon unchanged: evaluación de comportamiento sin cambio de producto/arquitectura; los tres hashes del release se verificaron contra registro.

Pendiente: corrección y revalidación de exportación múltiple; definir corrección auditada del período ya persistido sin duplicados; corregir experiencia de cola CP; completar las comprobaciones fiscales/presupuestales reales y validar el archivo COI con Contabilidad. La póliza interna por sí sola no satisface el resultado solicitado por el usuario.
