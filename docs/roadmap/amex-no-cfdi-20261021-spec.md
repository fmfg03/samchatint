# Especificación: AMEX directo «Sin CFDI»

Fecha: 2026-10-21. Estado: SPEC_APPROVED. Aprobación humana explícita: «aprobada» el2026-10-21, incluyendo el tratamiento contable y los textos exactos de §10. Historia aprobada: `docs/roadmap/amex-no-cfdi-20261021-story.md`, respuesta «correcto, dale». Esta propuesta no es implementación ni aprobación fiscal general.

## 1. Baseline, aprobación y límites

Base limpia inspeccionada: `fac1d1db361f7dc27cd94409f801afef3e9881c7`, coincide con origin/main recién consultado y con el release observado `/srv/samchat/releases/gastos-prod-fac1d1db3-amex-ranking`. Runtime: copa_telmex_dashboard.py/samchat-gastos.service; dominio AMEX directo; persistencia Postgres. Checkout principal sucio preservado. Construcción posterior en worktree nuevo aislado; no mezclar multiempresa, PASE ni otros cambios pendientes.

Canons leídos en orden, SHA-256 verificados contra registro de origin/main: producto65f8116a…, ingeniería8eebb3c5…, sweep2c55cc80…. Investigación local y revisión especializada sólo lectura; sin consulta de filas financieras ni modificaciones productivas para preparar esta especificación.

El usuario aprobó historia y especificación, incluidos el tratamiento monetario y los textos exactos de canon de §10. Esta copia versiona el objeto aprobado para construcción y pruebas; no acredita implementación terminada ni aplicación productiva.

## 2. Comportamiento aprobado

Contabilidad selecciona uno o varios cargos del mismo estado y confirma «Sin CFDI» con motivo obligatorio. Sólo está disponible para los operadores ya autorizados a conciliar/vincular CFDI en esa tarjeta y entidad; reutiliza la acción canónica `conciliar` y la exigencia Finance existente, sin nuevos roles ni concesiones. Control Presupuestal no obtiene esa autoridad por poder asignar partidas.

**Tratamiento aprobado:** contabilizar el importe completo del cargo como gasto contra el pasivo de la tarjeta. No generar movimientos de IVA acreditable, IVA pendiente, retenciones ni propina. No estimar impuestos ni declarar que existe un CFDI exento o a tasa0. No inferir deducibilidad ni elegir una cuenta de «no deducibles» automáticamente. La cuenta de gasto procede de la partida que asigne Control Presupuestal; la contrapartida procede de la tarjeta, como en el flujo vigente. Si el mapping no soporta ese tratamiento o falta, el preview muestra la excepción y no confirma póliza.

Esta decisión aplica a ausencia de CFDI. Un CFDI PPD sin complemento conserva el tratamiento AMEX ya aprobado: no se convierte en Sin CFDI ni pierde el IVA sustentado por su XML.

La declaración resuelve únicamente el requisito de vínculo fiscal del cargo nuevo. Continúan proyecto/categoría, edición, partida/mapping, fecha/período abierto y confirmación del corte completo. No excluye el cargo del estado ni confirma automáticamente su póliza o pago.

## 3. Estado y experiencia de uso

Acción sobre la selección existente de cargos: «Marcar Sin CFDI». Formulario con motivo de tamaño limitado validado en servidor, explicación del tratamiento anterior y confirmación explícita; POST con CSRF, fuente/contexto/versiones e idempotencia. Una selección inválida revierte el lote completo, sin resultados parciales. El detalle conserva cargo, fechas, importe, responsable y motivo; contadores distinguen pendiente de factura, Sin CFDI declarado y pendientes de presupuesto/contabilización.

Sólo cargos débito válidos de gasto nuevo, con contexto fiscal autorizado y estado aún modificable. Rechazar créditos, filas desconocidas, importes/monedas no admitidos, CFDI o UUID ya declarados, aplicaciones fiscales compartidas, propina/impuestos manuales, cobertura histórica seleccionada, reconocimiento previo o corte/recibo final. No borrar evidencias contradictorias para forzar elegibilidad. Se conserva la evaluación previa R2/pasivo reconocido y R3/cancelación histórica; Sin CFDI no los suplanta.

Permitir declarar antes o después de clasificar, pero antes de enviar/asignar a presupuesto. Si ya está enviado/asignado, usar devolución/liberación presupuestal existente antes de cambiar tratamiento; no retirar una asignación silenciosamente.

«Volver a buscar factura» registra una revocación explícita con motivo y confirmación; nunca borra la declaración anterior. Mismas restricciones previas al presupuesto/corte. Posterior reclasificación/presupuesto no revoca la decisión por sí sola. Si cambia contexto fiscal o cobertura económica, exigir revocación previa mediante la autoridad vigente y nueva declaración; ninguna reconfirmación automática.

Tras un corte final no se permite marcar, quitar o convertir a CFDI. La corrección posterior requiere un proceso contable separado fuera de esta entrega; no sobrescribir snapshots ni crear un segundo reconocimiento.

## 4. Modelo mínimo y dueño de escritura

Un servicio propietario nuevo `amex_direct_no_cfdi_service.py` gestiona declarar/revocar, con los helpers canónicos load_statement/load_items/require_unfrozen/record_event. No hace commit; el dispatcher conserva la UOW. No crea otro ledger.

Migración owner-run propuesta `20261021_amex_direct_no_cfdi.sql` y rollback permitido únicamente sin evidencia en los objetos nuevos, sin backfill:

- Tabla append-only `amex_direct_no_cfdi_decisions`: UUID propio; item/import/effective-expense/context y versión; revisión; acción declare/revoke; decisión anterior; tratamiento fijo `full_amount_expense_no_tax`; motivo; actor/fecha; petición/payload/idempotencia; huella económica de fuente y evidencia de versiones utilizadas. FKs reales, revisión positiva, motivo no vacío, tipos de acción/tratamiento cerrados, cadena del mismo item e idempotencia única verificable. El UUID del recibo es identidad interna y nunca UUID fiscal.
- Campo nullable `amex_direct_items.no_cfdi_decision_id` a la cabeza actual de esa cadena, incluyendo revocaciones. Sólo action=declare significa tratamiento activo. Una nueva revisión inserta evidencia; no modifica/borrra la previa.
- Campo nullable `amex_direct_cut_items.no_cfdi_decision_id`, FK al recibo exacto declarado, obligatorio para la nueva cobertura `new_no_cfdi` y ausente en las coberturas anteriores.

La huella económica liga archivo/row receipt, import, tarjeta, fuente efectiva, importe/signo/moneda, fecha, descriptor, gasto importado, contexto y recuperación auditada aplicable. Excluir expresamente item.source_hash, source_version mutable, clasificación, asignación presupuestal y mappings de cuenta de esa huella económica: clasificación y presupuesto los cambian normalmente. Sus versiones observadas se conservan separadas en el evento; preview/corte sí ligan la cabeza actual, item/source/version y decisiones actuales.

`item_hash` incluye la referencia a decisión sólo cuando exista, preservando exactamente el cálculo anterior para filas sin esa referencia. No reescribir hashes, decisiones o cortes históricos. La relación effective_sources puede permanecer sin cambios: consultar el recibo desde el item base en lote, sin un nuevo universo de fuente ni N+1 por celda.

Registrar tabla nueva en exclusión de startup DDL; ningún ensure_schema crea ni migra estos objetos. Actualizar modelos, imports y verificador de esquema con checks/PK/FK/uniques/triggers/funciones exactos. Huellas obtenidas de PostgreSQL desechable con el DDL instalado, incluyendo variante TEXT histórica cuando corresponda; no normalización genérica ni tolerancia de guards debilitados.

## 5. Integración presupuestal, reconocimiento y exportación

`list_budget_direct_movements`: para declaración activa y fuente válida, proyectar el importe bancario completo como pendiente/compromiso bajo la misma clave `expense:<id>`; no llamar al preview fiscal que actualmente deja el importe N/D. Las demás filas conservan sus reglas. Asignación/edición/scope usan el owner actual.

`_prepare`: resolver primero prior y settlement existentes. En la rama restante, validar declaración sin CFDI o exigir matching fiscal canónico. La rama `new` continúa exigiendo CFDI, XML, vigencia, disponibilidad global e impuestos reales. No se convierte un error de parsing/recepción/vigencia en ausencia de factura.

Para `new_no_cfdi`, exigir decisión activa y fuente económica actual, gasto activo y sin identidad/aplicación fiscal; presupuestación/mapping completos; fecha de estado y período abierto. Construir exactamente débito al gasto por el importe bancario completo y crédito por el mismo importe al pasivo de esa tarjeta, con metadata por item/gasto/asignación/decisión. Sin líneas fiscales o compensatorias inventadas.

La confirmación reutiliza AmexRecognitionConsumption statement/direct, representación, póliza y corte actuales, con sus unicidades y anclas bancarias. No existe CFDI artificial ni consumo económico paralelo. Los actuals presupuestales ya consumen source_kind=amex_direct y budget_assignment_id: mantener ese reader y comprobar que gasto pendiente se transforma en reconocido una sola vez.

Snapshot íntegro y por cargo: declaración/actor/motivo/tratamiento, fuente/contexto/versiones, cuenta y contrapartida, importe y movimientos congelados; cfdi ausente, no una identidad vacía presentada como CFDI válido. Corte sólo Sin CFDI y corte mixto con new/prior/settlement/credit_exclusion deben producir una sola póliza nueva válida y exportaciones completas.

`amex_cut_export_service` ya permite CFDI ausente y movimientos explícitos desde snapshot; conservar XLSX/CSV/ZIP, granularidad y RFC limpio. Añadir trazabilidad del tratamiento Sin CFDI sin inventar RFC emisor, folio, UUID, fecha o base fiscal. No consultar datos mutables para reconstruir un corte anterior. Motivos se renderizan como texto escapado en HTML/exportaciones, nunca como fórmulas de hoja de cálculo. Sin nuevos datos fiscales sintéticos para DIOT ni cambios a sus reglas.

Matching manual/automático y sugerencias respetan la declaración activa. Mostrar el estado en la fila; buscar/ver propuestas no debe revocar una declaración. La confirmación de vínculo exige revocación explícita previa. Nuevas facturas SAT no alteran automáticamente tratamientos o cortes.

## 6. SQL, concurrencia y seguridad

Mantener import/contexto → lock global AMEX → items ordenados y owners existentes, sin llamadas de red bajo locks. Declaración/revocación, matching, clasificación, envío/retorno presupuestal y confirmación usan el mismo estado/versionado y locks; una operación concurrente incompatible pierde por stale/conflict, no confirma dos tratamientos.

Nuevos guards: inserción de evidencia con cadena/actor/origen/confirmación coherentes; inmutabilidad UPDATE/DELETE; comprobación diferida de decisión/cabeza/evento; pointer del mismo item y transición permitida. Para una cabeza declare activa, contexto/huella económica deben coincidir con los actuales. Una cabeza revoke conserva el contexto/huella históricos y puede mantenerse durante el cambio posterior de contexto/cobertura; la siguiente declare liga la nueva fuente/contexto actual y referencia ese revoke anterior del mismo item. No exigir que un recibo revocado histórico adopte el contexto nuevo. Excluir estados finales y evidencia fiscal o histórica contradictoria. Proteger también vínculo de CFDI por expense_reports mientras haya declaración activa, para impedir un bypass fuera del dispatcher.

Actualizar definiciones vigentes mediante migración nueva, sin editar migraciones históricas:

- `amex_direct_projection_guard`: referencia/tratamiento y exclusión mutua; freeze conservado.
- `amex_direct_cut_item_guard`: cobertura `new_no_cfdi` con recibo/consumption/journal reales; las otras coberturas conservan su contrato.
- `amex_direct_check_cut`: incluir nuevo subtipo en relación consumption/corte, cobertura económica completa, mapping/edición/presupuesto y agregación exacta del pasivo. Para ese subtipo comprobar exactamente las dos líneas, importe, cuentas, ausencia de líneas fiscales, igualdad snapshot/ledger y recibo activo de fuente actual. Preservar sin ampliaciones la rama fiscal `new`, retenciones y uso compartido, R2/R3 y crédito excluido.
- Recuperación de período y cambios de contexto: rechazar mientras exista declaración activa; después de revocación conservar evidencia anterior y exigir nueva declaración para la nueva fuente/contexto.

Revisar guards de consumo, representación y freeze y mantenerlos si sus predicados ya abarcan la nueva cobertura; probarlo con SQL negativo en lugar de relajarlos por anticipado. Una excepción tardía debe revertir todo incluso si el caller captura el error y pretende confirmar. La evidencia final no se borra ni se transforma para volver a verde.

## 7. Archivos y fases previstos

Modelos/política: amex_direct_models.py, models.py, amex_schema_policy.py. Servicio nuevo de decisión; hunks necesarios en amex_direct_common.py, amex_direct_accounting_service.py, amex_direct_budget_service.py, amex_direct_matching_service.py, amex_direct_suggestion_service.py, amex_direct_context_service.py/amex_direct_recovery_service.py cuando sus transiciones lo requieran, owner de vista vigente y amex_cut_export_service.py sólo para evidencia textual congelada. Ruta admin_amex_direct_routes.py con dispatcher/formulario existentes; inventario/matriz de acciones donde el cambio real lo exija, sin nuevas concesiones. SQL forward/rollback, schema_service/contract, pruebas y documentos.

No cambiar expense_accounting_service ni el matching legacy para permitir genéricamente gastos sin CFDI. No modificar frontend ajeno, informes ordinarios, Payment Run, pago AMEX, multiempresa o PASE.

Fases después de aprobación: contrato/migración/owner y pruebas de integridad; integración presupuesto/preview/confirmación/export; UI usando estilos actuales y acciones agrupadas; aceptación real en PostgreSQL y navegador; revisión independiente y reporte final antes de PR. Sin dependencias o jobs nuevos.

## 8. Plan de aceptación

- Declarar/revocar/redeclarar un cargo y un lote; motivo/confirmación/versiones/CSRF; replay idéntico y conflicto de payload. Actores inactivos, sin rol/scope, entidad/tarjeta ajenos se rechazan antes de efectos.
- Contradicciones: CFDI/UUID/uso compartido/propina/impuestos, gasto ya reconocido, fuente prior/settlement, abonos o fila desconocida. Mantener evidencia y mostrar causa, no limpiar automáticamente.
- Recorrido declarar → clasificar → asignar partida → confirmar conserva la vigencia de la declaración; presupuesto usa importe completo, cuenta de la partida y pasivo de tarjeta, sin dobles actuals. Tratamiento no cambia mientras está enviado/asignado sin devolución previa.
- Recorrido revocar → cambiar contexto o cobertura recuperada → redeclarar preserva la cabeza revocada histórica hasta la nueva declaración. Los cambios de fuente/contexto/cobertura recuperada requieren esa revocación y nueva declaración. Matriz declaración vs matching/recovery/contexto/confirmación concurrentes, sin forks ni reconocimiento doble.
- PostgreSQL de verdad con guards activos: intentos SQL directos de falsificar recibo/pointer/cobertura/cuentas/importes/líneas fiscales y mutaciones después de corte fallan; rollback atómico y deferred checks forzados antes de cerrar savepoint.
- Un estado sólo Sin CFDI y uno mixto nuevo-con-CFDI/Sin-CFDI/prior/settlement/créditos con importes exactos; preview READ ONLY sin DML/DDL/locks/SAT; confirmación balanceada y un único consumo/presupuesto/pasivo por cargo.
- Hashes previos de items sin pointer y snapshots/exportaciones históricos permanecen idénticos. XLSX/CSV/ZIP desde snapshot con movimientos exactos, declaración trazable y campos fiscales ausentes; reexport conserva evidencia aunque cambien catálogos. Pólizas anteriores y exportaciones CFDI/R2/R3 mantienen sus pruebas.
- Recorrido navegador: seleccionar cargos → Sin CFDI/motivo → clasificación → partida por autoridad CP → preview → confirmar → descargar; pantallas con mensajes de bloqueo y sin selección individual obligatoria por cargo.

Usar .venv pytest/coverage y herramientas existentes Black88/isort/flake8/mypy focales. Cobertura modificada mínima85%; PostgreSQL desechable con lease y guard de target (nunca pruebas con escrituras productivas). No se han ejecutado aún estas pruebas: éste es el plan para revisión. Reporte final incluirá archivos, comandos, resultados, fallos atribuidos, desviaciones y riesgos.

## 9. Migración y despliegue posteriores

Necesita migración owner-run: tabla de decisiones, referencias y guards/contratos. Sin backfill ni cambio de registros de negocio. Especificación aprobada autoriza construir y probar; aplicar la migración, publicar y desplegar requiere la autorización concreta de esa fase. No se marcarán automáticamente cargos reales del usuario como Sin CFDI al instalar.

Después de validación: commit exacto integrado, respaldo privado verificado, pausa de callers/monitores pertinentes, migración transaccional con lock AMEX y verificador del release nuevo listo antes de commit; activación y comprobación de WorkingDirectory/HEAD/drop-in único/healthz/readyz/guards/configuración. Conservar releases/assets anteriores y no arrastrar cambios del checkout sucio.

Rollback de esquema se niega si hay decisiones/revocaciones/cortes reconocidos; no borrar historia. El código anterior no entiende este tratamiento ni los hashes nuevos: no prometer downgrade seguro después de instalar o usarlo. Recuperación compatible hacia adelante; restauración de funciones/esquema vacío sólo en procedimiento owner-run explícito y comprobado. La UAT de Contabilidad con cargos reales se informa separada de pruebas técnicas.

## 10. Textos exactos de canon propuestos para aprobación

Textos exactos aprobados por el usuario el2026-10-21 y aplicados en esta entrega. Razón: nueva resolución explícita de cargos sin CFDI que antes bloqueaban el gasto nuevo. Evidencia: historia aprobada y esta especificación del2026-10-21. No se declara implementación, migración, publicación, despliegue o aceptación financiera con su aprobación.

Producto, apéndice AMEX:

«AMEX directo permite que un operador autorizado de Contabilidad declare explícitamente un cargo nuevo Sin CFDI, con motivo, tratamiento y evidencia bancaria conservados. La declaración no fabrica factura, identidad fiscal o aprobación presupuestal. Para el tratamiento importe completo como gasto sin movimientos fiscales, la partida presupuestal determina la cuenta de gasto y la tarjeta determina el pasivo; no se estima IVA, retención, propina ni deducibilidad por ausencia de factura. El estado completo mantiene sus controles y una póliza inmutable. Una factura recibida después no altera una declaración o corte automáticamente; antes del corte se requiere revocación explícita y las correcciones posteriores conservan su autoridad e historia.»

Ingeniería, apéndice AMEX:

«Sin CFDI se representa mediante decisiones append-only, cabeza actual versionada y vínculo real del miembro de corte a su declaración. La cobertura new_no_cfdi comparte los owners de fuente bancaria, presupuesto, consumo, póliza y exportación; exige fuente/contexto vigentes, autoridad y motivo, presupuesto/mapping y movimientos exactos de gasto/pasivo sin identidad ni líneas fiscales inventadas. Guards y transacción impiden tratamientos simultáneos, cobertura parcial, doble reconocimiento y mutación de evidencia final; la rama new conserva íntegramente su contrato CFDI. Esquema e integridad se provisionan mediante migración owner-run y metadata-contract exacto, nunca DDL de startup ni reparación histórica implícita.»

Registrar evidencia/fecha/razón y actualizar sólo hashes de canons modificados después de aprobación humana de estos textos. Sweep: Canon unchanged, reporte histórico sin nueva observación de su baseline.

## 11. Revisión previa de la propuesta

Revisión especializada sólo lectura: sin hallazgos Critical. Se corrigieron antes de presentar la especificación dos ambigüedades importantes: cabeza revocada histórica frente a contexto futuro y exclusión de hashes/versiones mutables de la huella económica. También se precisaron nombre real del owner de recuperación, rollback guardado y pruebas de esas transiciones. No se implementó código, ejecutaron pruebas ni alteraron canons o producción. La aprobación técnica y de los textos exactos de §10 quedó registrada el2026-10-21; la revisión descrita antecede a esa aprobación.
