# Historia aprobada: AMEX directo desde estado/SAT a presupuesto, póliza y pago

Fecha: 2026-10-14. Base investigada: `71247de778ba2c69783cd68427a7084c01e7c42f`, runtime `samchat-gastos.service`/`copa_telmex_dashboard.py`, dominio gastos/AMEX + presupuesto + Payment Run, PostgreSQL directo. Canons leídos en orden y hashes coinciden con registro. Fase actual: historia y especificación técnica aprobadas por el usuario el 2026-10-15; implementación autorizada, con aprobación final y promoción separadas. Las autorizaciones de PR483/484 ya se cumplieron y no se presentan como autorización de esta ampliación.

## Intención del usuario

Como Contabilidad, quiero cargar un estado de cuenta y ver todos sus cargos con los CFDI descargados del SAT que correspondan, conciliados automáticamente cuando exista una coincidencia inequívoca, sin exigir ni fabricar un INFORME de gastos. Quiero seleccionar proyecto/torneo y una categoría del catálogo para el cargo y enviarlo a Control Presupuestal para que Juan Pablo asigne su partida; esa asignación debe resolver automáticamente la cuenta del gasto y usar como contrapartida la cuenta de la tarjeta. Después necesito preparar/confirmar la póliza y seguir pagando la tarjeta desde la pantalla existente.

## Recorrido propuesto

1. **Cargar y conciliar:** tarjeta y período explícitos, archivo conservado, cargos íntegros, CFDI SAT canónico identificado, resultado automático o motivo de pendiente. Reintentar no crea cargos ni vínculos duplicados.
2. **Clasificar y presupuestar:** Contabilidad selecciona una categoría del catálogo; envía cargos revisados a Control Presupuestal. Contabilidad indica explícitamente el proyecto o torneo y selecciona la categoría válida de su catálogo. Juan Pablo, mediante la autoridad real configurada, asigna la partida del presupuesto correspondiente. Se resuelven cuenta del gasto desde su catálogo y contrapartida desde la tarjeta declarada; falta/contradicción de mapping queda visible.
3. **Póliza y pago:** vista contable trazable desde estado, cargo, CFDI y asignación presupuestal; confirmación financiera y exportación con evidencia inmutable. Acceso contextual a «Programar pago de AMEX» y Payment Run existentes. Asignar una cuenta no acredita por sí solo generación/importación de póliza ni pago.

## Criterios de aceptación

- El recorrido principal parte del estado; no requiere elegir/crear/aprobar un INFORME ni crear uno ficticio internamente.
- La vista muestra todos los cargos del archivo y su identidad original. Incluye fecha, descripción, importe/moneda, tarjeta, CFDI y estado/motivo de conciliación, concepto, estado de envío presupuestal, partida, cuenta/contrapartida y avance contable.
- Matching usa CFDI ya descargado SAT de la entidad/receptor autorizado, evidencia fiscal y moneda/importe coherentes. Una coincidencia única sólo se confirma automáticamente conforme reglas explícitamente aprobadas; igualdad de importe o un score heurístico aislado no bastan. Reglas exactas de fecha, tolerancias y datos disponibles se fijan en especificación, sin inventarlas.
- Falta de factura, ambigüedad, uso previo incompatible, importes compartidos o distintos, identidad contradictoria y vigencia desconocida permanecen visibles con resolución específica. No se convierten silenciosamente en propina, cero, gasto deducible o vínculo definitivo. Factura compartida declarada requiere controles de distribución; no se confunde con duplicado.
- Contabilidad puede seleccionar una categoría activa del catálogo y enviar a Control Presupuestal sin recapturar las facturas ni los cargos. No se asigna proyecto o beneficiario por nombre del titular/capturista sin evidencia.
- Contabilidad asigna el proyecto o torneo; Juan Pablo asigna la partida dentro de ese contexto mediante identidad/permisos/scope vigentes; su nombre no concede autorización. Se usa el presupuesto y proyecto seleccionados explícitamente.
- La cuenta del gasto deriva del mapping activo de la partida/concepto presupuestal; la contrapartida deriva de la cuenta pasivo activa de esa tarjeta. No se inventan cuentas por texto ni se pisa un mapping contradictorio ya reconocido. Impuestos conservan su evidencia CFDI y no se infieren desde el presupuesto.
- El origen contable directo es estado+cargo+CFDI+clasificación+asignación presupuestal+confirmación Finance. No se elimina un guard del owner anterior para aparentar que un informe existe. Granularidad y tratamiento de cargos pendientes se definen y aprueban antes de implementar generación/exportación.
- Si el mismo consumo ya está reconocido mediante un informe/corte o una fuente previa, se conserva su evidencia y se evita otro reconocimiento de gasto/pasivo. Los cargos bancarios siguen visibles. Historial congelado y los 14 casos protegidos no se reparan/backfillean en esta entrega.
- Se conserva la pantalla «Programar pago de AMEX» en `/admin/gastos/amex/conciliacion`, que envía a Payment Run. La programación no se muestra como pago realizado. El pago liquida la tarjeta mediante su owner actual y no vuelve a reconocer gastos. Esta historia no agrega nuevos bloqueos de pago por falta de CFDI.
- UI utiliza el marco compartido SamChat y hace explícito quién debe atender cada pendiente. Matching/carga, clasificación, presupuesto, póliza y pago son hechos separados y auditables.

## Evidencia actual y reutilización

- Importación ya intenta vínculo fiscal automático en `amex_statement_import_service.import_amex_csv_rows` → `amex_statement_matcher.find_amex_cfdi_match`, sin INFORME. Su heurística actual no prueba conciliación definitiva: no acota origen SAT/receptor/moneda/vigencia, no rechaza empate y permite reutilización entre cargos batch.
- SAT ingiere XML canónico mediante `ingest_cfdi_xml(source='sat')`; guardas de UUID/evidencia compartida y conceptos/impuestos se reutilizan. Estado de descarga SAT no acredita vigencia del CFDI; la consulta de vigencia existente no persiste resultado observado en modelos revisados.
- Control Presupuestal actual parte de Documento; la asignación de movimientos y cuenta desde `BudgetConcept` ya existe, pero debe conectarse a origen AMEX directo con su autoridad/evidencia propias.
- `create_amex_card_payment_request` ya crea solicitud para Payment Run, no acredita pago ni contabiliza automáticamente por sí sola.
- Pólizas/cortes actuales del recorrido principal requieren INFORME aprobado. Cambiar ese requisito requiere nuevo contrato financiero y revisión expresa de los textos afectados de canon, sin enmiendas silenciosas.

## Preguntas abiertas para especificación

1. Resuelto por el usuario: «concepto» de Contabilidad es categoría de catálogo. Investigar catálogo canónico adecuado y reutilizar su identidad; no introducir texto libre como sustituto.
2. Resuelto por la corrección más reciente del usuario: Contabilidad asigna proyecto/torneo y categoría del catálogo; Juan Pablo asigna únicamente la partida presupuestal. Esta decisión sustituye la atribución anterior a Juan Pablo.
3. Identidad entidad/receptor por tarjeta/estado y evidencia de vigencia SAT: definir fuente real, sin asumir RFC de e.firma ni ampliar descargas automáticamente.
4. Reglas de matching seguro, repartos, propinas/moneda/créditos y granularidad/fecha de la póliza directa: presentar política concreta para aprobación técnica.

## Fuera de alcance salvo nueva aprobación

Reparación productiva, reversión de pólizas existentes, borrado/reasignación de vínculos/cargos, relleno retroactivo de informes, descarga SAT nueva o ampliada, cambio de reglas de reembolso/otros documentos, automatización del pago bancario, multiempresa global o nuevas dependencias sin aprobación.

## Validación y promoción

Pruebas unitarias de política; PostgreSQL de idempotencia/concurrencia/ownership/CFDI/cuentas/presupuesto; integración del origen directo y no duplicación de report-first; navegador por roles; validación independiente y revisión humana del canon. Migración si la especificación requiere persistencia nueva, con backup/owner-run/rollback y autorización propios. No se promete «sin migración» para este nuevo alcance. CI, merge, deployment y UAT de Contabilidad/Juan Pablo/Finanzas son etapas posteriores.

## Trabajo adicional separado

El usuario solicita corregir espacios adicionales del RFC en Excel de pólizas de SOLICITUD e INFORME. Esa corrección de formato se investiga por separado: no modifica este flujo, datos fiscales originales ni el layout COI. Su diagnóstico/aprobación/publicación tienen evidencia propia.


## Continuidad de aprobación — 2026-10-15

El usuario aprobó explícitamente esta especificación y sus textos exactos de canon con «aprobada», después de la revisión independiente sin hallazgos pendientes. Se autoriza implementar y probar en worktree aislado basado en main `d8c9fda23ed1b1bb1a68a08bc524fedbdb6d4e5f`; no aplicar migración, reparar producción, publicar ni desplegar. La fecha original del diseño permanece 2026-10-14.
