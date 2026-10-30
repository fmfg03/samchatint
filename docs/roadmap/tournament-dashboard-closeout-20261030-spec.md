# Cierre de tableros de Liga, Homeless y Copa

Fecha: 2026-10-30. Historia autorizada por el usuario: «pues vamos a resolverlo de una vez», en respuesta al alcance de cuentas finales, vínculo contable, conciliación, comparación y despliegue. **Especificación técnica y las 13 propuestas de Copa aprobadas explícitamente por el usuario: «Aprobar plan y las 13 propuestas de Copa». Implementación autorizada; la corrección productiva aún no se ha aplicado.**

## 1. Resultado y límites de aceptación

Cerrar lo que puede acreditarse con los archivos ya entregados, conservar los importes y estados de pago y dejar las excepciones concretas en conciliación. No volver a solicitar presupuestos ni catálogo. La entrega técnica no se describirá como totalidad contable aceptada mientras falten fuentes o decisiones de negocio.

- AC1: completar únicamente las 40 cuentas finales identificadas por UUID, sin cambiar importes, referencia, variación, concepto, fase ni dirección; no reactivar retirados.
- AC2: conservar distribución Liga semana 40, Homeless semana 52 y Copa semana 52. Ninguna lectura puede observar cuentas corregidas con una distribución invalidada.
- AC3: completar la proyección presupuestal de los tres movimientos de Hospedaje de Liga ya decididos y auditados. Otros movimientos requieren una decisión específica acreditada; una metadata editable no la sustituye.
- AC4: exponer las cuentas históricas documentadas aunque no exista una partida activa, sin crear conceptos ficticios ni atribuir por defecto cuentas compartidas a un torneo.
- AC5: separar presupuesto IVA incluido, gasto fiscal, compromiso/pagado documental e importe contable registrado. Sin equivalencias falsas, tasa global, doble conteo, caja inferida o ceros para fuente ausente.
- AC6: mismo alcance, periodo y condición de disponibilidad en pantalla, asistente y export afectados. Pruebas, validación independiente, CI, merge, despliegue y verificación productiva separados de aceptación autenticada de Finanzas.

## 2. Base y evidencia cerrada

Runtime: `samchat-gastos.service`, `copa_telmex_dashboard.py`, PostgreSQL `devnous_db`. Release inspeccionado: `6ef12b10316aec43d8edaed64ff5715a2154c3ea`, activo, NRestarts=0. Candidato aislado desde main `3926e4f71`; los owners de presupuestos y Dirección coinciden con el runtime salvo un delta previo de `admin_budget_routes.py`, que se preservará sin copiarlo a una promoción selectiva.

Los tres canons del release se leyeron en orden y sus SHA coinciden con su registro: producto `4f369621a03290d320c9b9efbd5f16683bcfb6e50d99bc5bae1e3d8e7d45345e`, ingeniería `543dc342e52e0525015e0720904411beab38ac8dc9ed8d4c011acaa13b838225`, sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`.

Versión financiera: `64d963a7-61b1-402a-b58a-7efcb96cd71a`, aprobada. Totales preservados: Liga 3,686,827.60; Homeless 30,384,939.78; Copa 17,471,291.92. La nota Borrador se conserva.

Paquete de revisión: `/root/samchat/tests/artifacts/cierre-tableros-20261030/Revision_cierre_tableros_20261030.xlsx`, SHA `3c6c4c2254b46e7f804748e25736edbb4cdd036f2056376f804482008d6040f5`. JSONs de soporte: `cuentas-finales-40.json`, `cuentas-contables-85.json`, `all-39-financial-review-enriched.json`, `shared-cfdi-source-review.json`, `budget-line-metadata-provenance-review.json`, `weekly-overlays-before.json`, `investigacion-manifest.json` y `research-financial-closeout-manifest.json`. Son investigación READ ONLY, no un recibo de aplicación ni una aprobación de equivalencias.

Fuentes fijadas: catálogo (3) SHA `75da03a5853199c3fab3db96699ed1950bb6b637b2702630b8bbcfb24e3d4eae`; BGPSP SHA `5e66cbf4825a985aebf6cfc7049639c0e259b09d76f5032d1cb58463f5955652`. Conservar hojas/filas y recibos originales de importación. Revalidar bytes y selección antes de aplicar.

## 3. Corrección de las 40 cuentas y semanas

Son 36 líneas Liga (2,380,538.60) y 4 Homeless (5,292,923.09). En 32 la cuenta surge directamente del concepto activo y su cuenta activa. En las 8 restantes de Liga se completa únicamente la cuenta respaldada por el catálogo aprobado, conservando el concepto histórico retirado:

| Concepto/fase histórico | Cuenta | Filas catálogo 3 |
| --- | --- | --- |
| Pelotas / Estatal, Nacional, Home Run Derby | 5300-012-009 | 776–778 |
| Uniformes / Estatal, Nacional | 5300-012-008 | 13, 1191 |
| Box Lunch / Nacional | 5300-012-018 | 203 |
| Cuotas de Asociaciones / Generales | 5300-012-010 | 294 |
| Trofeos y medallas / Nacional | 5300-012-023 | 584 y 1161 |

La última cuenta es común a ambos conceptos: no se reparte ni duplica el importe agrupado. Se registran sin alterar la fase distinta de Uniformes en `39658814-9b26-49ad-aad9-e2c433e009f7` y la etiqueta histórica HWC en `0728a2f8-a64d-49fe-b53b-c2e93762b9c2`.

No basta con actualizar códigos: forman parte de la huella semanal. El operador debe componer los owners actuales, sin un writer paralelo ni un salto del guard:

1. Preview READ ONLY: fuente exacta, versión seleccionada, actor Juan Pablo previamente autorizado (`e3d13040-2360-420f-98a1-516440ef63c3`) activo y con permisos actuales, 40 filas, cuentas destino activas, revisiones vigentes e invariantes.
2. Ensayar en copia restaurada y transacción exterior con savepoints: bloquear versión/líneas/revisiones y desactivar sólo las revisiones Liga/HWC por su UUID y plan hash mediante `deactivate_tournament_overlay`. Hoy ambas tienen `previous_effective_revision_id=NULL`; cualquier cambio obliga a rehacer preview y demostrar el nuevo recorrido, nunca a eludirlo.
3. Usar `transition_budget_version` hacia reforecast y `update_budget_line` con payload exclusivo `account_code_final`, actor y `ensure_schema=False`. Validar actividad y código de cuenta externamente porque el writer de línea no lo comprueba por sí solo. Probar que su recálculo de variación no altera nada.
4. Reaprobar mediante el owner, previsualizar y aplicar nuevas revisiones de Liga/HWC con las semanas originales. Conservar historia, nota, Copa y todos los importes. Si el guard de aprobación detecta un plan legacy incompatible, se bloquea la operación completa; no se agrega un permiso genérico para omitir validación.
5. Commit sólo si cuentas, estado final approved, semanas, sumas y lectores efectivos concilian. Fuente distinta, cuenta inactiva, campo adicional, fallo intermedio o revisión concurrente producen rollback completo. Repetición verificada no crea nuevos cambios.

No nueva tabla, dependencia, migración, modificación de cuenta de una póliza ni borrado de líneas.

## 4. Conciliación con evidencia de decisión

Hay 28 movimientos sin línea y 11 ya asignados pendientes de contabilización. Reasignar los 11 no genera sus pólizas y no es parte de esta reparación.

Los tres movimientos Liga de RO108/128/129 conservan conceptos/cuentas históricos en su póliza. La decisión humana anterior y el audit `2dac06db-93a1-4702-b1c1-00199024744b`, plan `b41034c8b83e64022a421119b160c86f4c15d8fee692421fddf97c8d670a94bd`, acreditan Hospedaje nacional: concepto `47e5272f-3a2b-44ae-9ecb-b3e6ccf9a2e4`, línea destino actual `a68157c6-05d5-40cc-b709-bab007f39495`, cuenta presupuestal 018. Reutilizar esa decisión, sin pedirla otra vez y sin reescribir la póliza ni la solicitud pagada.

Usar `assign_budget_movement_to_line` y las claves específicas `accounting:UUID`, con torneo, versión raíz, fase y línea destino revalidados. Conservar `budget_movement_assignments` y su auditoría. No ampliar a otros movimientos del mismo documento por semejanza.

En Copa hay 13 propuestas con un único destino compatible en la hoja `Copa propuestas13`, pero su `consolidated_into` editable no acredita aprobación. **Sólo se aplican esas 13 si el usuario aprueba expresamente esa hoja/lote además de esta especificación.** Fijar la decisión a sus claves, destino y hash del paquete; de lo contrario son únicamente propuestas de revisión.

Los 12 casos restantes del paquete tienen destino ambiguo o no acreditado: permanecen en la cola de conciliación con referencia, fase y candidatos. No crear una línea, repartir presupuesto, cambiar fase ni seleccionar por importe/proveedor/nombre para eliminarlos de la cola.

## 5. Lectura contable documental independiente de partidas

Las 85 cuentas de las ramas 5300-010 (Copa), 5300-012 (Liga), 5300-016 (Homeless) están documentadas en BGPSP y la balanza 2026 PSP, empresa 01. De ellas, 27 no tienen concepto activo del torneo: no fabricar conceptos ni reactivar retirados para mostrarlas.

Ampliar el owner existente `client_executive/accounting_bridge.py` con un registro explícito versionado de alcance **documental**, por empresa, ejercicio, cuenta y hoja/fila/hash de origen. Archivo propuesto `config/tournament_accounting_documentary_scope_2026.json`, sin importes de ejemplo. Este registro no equivale a `exclusive_account`, no escribe `approved=true` en bindings y no sustituye la autoridad de vínculos contables ya aprobados.

- Mostrar como cuenta del catálogo/importe registrado, no como gasto total exclusivo del torneo. Las 6 cuentas compartidas activas, la referencia retirada adicional de 5300-010-013 y la cuenta de año anterior 5300-010-020 se identifican sin atribuirlas a la edición/proyecto por defecto. Las restantes 77 tampoco reciben una atestación ficticia de exclusividad.
- Separar balanza oficial y pólizas. Fuente seleccionada aplicada, compañía 01 y ejercicio 2026 exactos; no unir versiones ni completar meses excluidos con históricos viejos. September cuenta con pólizas, no con una balanza oficial.
- La advertencia `missing_summary_marker` existe en las 986 cabeceras balanceadas del paquete PSP. Para el nuevo modo documental sólo puede admitirse esa advertencia informativa, conservándola visible, si la fuente está validada/hash fijado, póliza íntegra/balanceada y validación de admisión sin diferencias materiales. Otras advertencias no se aceptan por una whitelist genérica. No cambiar flags ni imports.
- Presentar débito/haber/importe registrado por cuenta y periodo con procedencia. Moneda y base fiscal contables no están acreditadas de forma global: no etiquetar los números como MXN, no sumar entre empresas y no producir variación contra presupuesto con esos importes. Ausencia de filas queda N/D, distinta de un cero explícito de balanza.
- Mantener separados los resultados del puente estricto y la evidencia documental nueva; no convertir una cobertura parcial en un total contable completo. Las lecturas conservan las facultades por actor/torneo/empresa y no hacen DDL ni escrituras.

## 6. Bases de IVA, lectura ejecutiva y compartidos

El presupuesto MXN/IVA incluido ya fue confirmado. Copa conserva fuente en `metadata.copa_20261028`, no en `upload_source`: compatibilizar el lector sólo cuando fila, hash de archivo/plan, `autorizaciones.json` y recibo comprometido coincidan. No inventar `upload_source` ni recalcular importes. HWC tiene 12 filas legacy de cero sin esa metadata. Liga tiene 56 ceros y tres importes positivos sin recibo de base individual (99,840; 312,000; 19,500); conservar sus valores y declarar la evidencia faltante.

Conservar Actual fiscal y periodo canónicos. Comprometido/pagado documental usan el importe pagable y su cohorte, no prueban caja; no se suman etapas del mismo hecho. Comparaciones sólo por poblaciones, periodo, moneda y base acreditadas compatibles. No multiplicar/dividir por 1.16 ni cambiar la definición fiscal del Real para obtener un delta.

Los compartidos requieren elegibilidad y aplicación global por UUID fiscal, incluso fuera del torneo seleccionado. El paquete de revisión identifica 9 gastos fuente y 6 CFDI, sin afirmar que sea una atestación global completa. El UUID 62380A3F-80F5-4A4C-B31D-7CD524312D3D tiene dos gastos fuente de 8,700 cada uno contra un comprobante de 8,700: no cancelar, repartir ni elegir uno por antigüedad. Mantener el total completo N/D mientras no haya conciliación acreditada; un subtotal conocido se etiqueta como tal.

Las 26 filas fijas del formato de referencia conservan sus IDs y N/D cuando falte crosswalk confirmado. El CSV de propuesta no es una aprobación Finance ni permite distribuir totales entre Partida1/Partida2.

## 7. Archivos y verificación

Alcance previsto: script guardado de conciliación en `scripts/`, owners `budgets/service.py` y `budgets/tournament_weekly_overlay.py` sólo si un cambio mínimo resulta necesario y justificado por aceptación; lectores `client_executive/accounting_bridge.py`, `home.py`, `ui.py`/export afectado y `budgets/executive_facts.py`/compatibilidad de procedencia sólo según dependencia comprobada; registro documental JSON; pruebas focales y este documento. No editar handlers de pagos, CFDI, reconocimientos ni `user_routes.py` por conveniencia.

Pruebas obligatorias: 40 campos exactos y demás filas idénticas; conceptos retirados intactos; cantidades, estados, UUID y fases conservados; rollback entre dos torneos; fuente/hash/actor/cuentas activas/carrera/idempotencia; semanas 40/52 y Copa inalteradas; tres decisiones Liga por audit real; metadata editable insuficiente; 13 Copa sólo por decisión del lote; compartidos/año anterior y cuentas sin concepto; separación moneda/base desconocida; quality flag informativo vs error material; meses retenidos y sin fallback; fuente Copa con recibo real vs recibo/archivo alterados; shared CFDI sin doble conteo; consistencia de lectura y export.

Ensayo PostgreSQL aislado, validación independiente READ ONLY antes de PR, checks obligatorios, revisión de comentarios, merge protegido y promoción selectiva sobre el release entonces servido. Respaldo productivo, preview fresco y hash de código/datos antes de aplicar; recibo posterior con selección, cuentas, sumas, semanas y preservación. No restaurar un dump global sobre actividad posterior. Recuperación según recibos/owners y auditoría, nunca reimportar a ciegas.

Canon unchanged: se concretan conciliación gobernada, fuentes documentales, ausencia de inferencias, historia y separación de bases existentes. No cambia autoridad financiera, estados de pago, reconocimiento, definición del gasto fiscal o reglas fiscales. Si el build necesitara cambiar esos contratos, ese delta se presenta para revisión humana antes de implementarlo.
