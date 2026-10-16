# Especificación técnica: estabilización del recorrido AMEX hasta COI

Fecha: 2026-10-15. **Especificación técnica y textos de canon aprobados explícitamente por el usuario con «aprobada».** Historia/assessment aprobados mediante «ok, aprobado». Base: `a983be79e1ad8a6b5aa56affe27a945160ac8166`. Investigación y reproducciones: `amex-accounting-journey-qa-20261015.md` y `docs/evidence/amex-accounting-qa-20261015/`.

## 1. Contrato y fases

Runtime objetivo: `copa_telmex_dashboard.py`/`samchat-gastos.service`. Dominio propietario: `src/devnous/gastos/`, PostgreSQL, servicios canónicos AMEX y presupuesto. El diseño conserva el estado directo como fuente, sin fabricar INFORME. La configuración autorizada de entidad/tarjetas/Finance/CP permanece vigente.

Construir en fases enfocadas: A exportación; B prevención/recuperación del período; C mesa de CFDI y clasificación; D CP/preview y aceptación integral. Cada fase conserva pruebas rojas previas y verifica los negativos pertinentes. Ningún código de aplicación ni DDL se ha modificado para preparar esta propuesta.

La aprobación técnica incluirá los cambios de comportamiento, esquema y textos propuestos de canon de este documento. No ejecutará por sí misma migración, recuperación ni decisiones fiscales/presupuestales productivas. No implica publicación/despliegue.

## 2. Exportación multicargo — AMEX-QA-08

`amex_cut_export_service.cut_expense_cfdis` compara hoy posiciones: el global agrupa débitos de varios cargos y después contrapartidas, mientras las partidas agrupan por gasto. El caso de dos cargos conserva seis movimientos equivalentes, pero XLSX/CSV/ZIP fallan.

Comparar cobertura económica mediante multiconjunto (`Counter`) de la identidad ya usada por el lector: UUID de cuenta, código, Debe y Haber como Decimal. Conservar multiplicidad exacta; no usar set, sólo sumas, tolerancias ni excluir líneas para hacer coincidir resultados. Exigir global y desglose no vacíos y que coincidan exactamente. Mantener validación COI, movimientos no negativos, agrupación y evidencia de CFDI. Exportar las partidas congeladas en su orden original; no reconstruir desde catálogos ni modificar snapshots/journals existentes.

Sin migración para esta corrección. Cubre cortes directos y regresiones de cortes INFORME que comparten lector. Probar uno/dos/muchos cargos, líneas iguales repetidas legítimas, orden distinto, faltantes/extra, cambio de cuenta/código/importe, desbalance y evidencia inválida. El reproductor de dos cargos debe pasar en los tres formatos; el caso de 145 debe producir archivo completo y una sola póliza, manteniendo replay y freeze.

## 3. Período: prevenir antes de guardar — AMEX-QA-01

Extraer un único evaluador puro compartido por preview e intake: parseo firmado actual más período explícito y comprobación de cobertura. Preview recibe también inicio/fin y devuelve fechas mínima/máxima, conteos y filas fuera del rango, con fecha/motivo. Conserva bytes, perfil y signos.

Para una **nueva carga**, si una fecha válida queda fuera del rango declarado, rechazar antes de crear import/filas/Expense con `statement_period_mismatch`. Mostrar cuántas filas y cuáles fechas requieren revisar el período; no ampliarlo automáticamente. Cambiar período invalida el preview anterior. Import vuelve a comprobar todo independientemente del navegador.

Replays de cargas anteriores con el mismo contexto siguen devolviendo su estado existente, incluso si fue capturado por la versión defectuosa. No crear duplicados ni convertir ese replay en una recuperación. Un hash existente con distinto contexto sigue rechazado por el intake ordinario.

Los casos de signo desconocido, moneda o filas inválidas conservan sus controles; no se descartan para aprobar el estado.

## 4. Recuperación auditada de un estado directo pendiente

### Decisión arquitectónica propuesta

**No cambiar el header, recibos originales ni fuentes físicas inmutables.** Tampoco basta cambiar una fecha de pantalla: los guards comprueban que cada ítem corresponda al status/ancla de su recibo original y rechazan todo skipped al confirmar.

Añadir un owner acotado de recuperación para filas DIRECT omitidas **exclusivamente** por `row_outside_declared_period`, con fuentes efectivas verificables. No es adopción legacy ni un motor general de reparación.

Migración owner-run `database/migrations/20261015_amex_direct_period_recovery.sql`, sin startup DDL ni backfill, añade:

- `amex_direct_coverage_revisions`: UUID; import FK UNIQUE; source-file FK a su import; hash original; parser/perfil; límites originales y corregidos; conteos originales/efectivos; conjunto exacto de ítems recuperables y conteo esperado; versión previa esperada; actor, motivo, idempotency/request hash, membership digest y timestamp. Una recuperación por estado en esta entrega; retry idéntico devuelve el mismo receipt, otro payload produce conflicto.
- `amex_direct_recovered_rows`: revisión FK; direct-item FK UNIQUE; import-item FK UNIQUE; Expense FK UNIQUE; número de fila; kind efectivo limitado a debit; fecha, moneda, celda/columna/importe firmado/perfil, `effective_source_json` parseado y hash de prueba exactos. Tablas append-only con FK RESTRICT y guards de pertenencia/economía/actor/versiones. No existe borrado posterior de receipts.
- Vista canónica `amex_direct_effective_sources`: exactamente una fila por direct-item físico. Para un ítem normal usa su fuente original; para uno recuperado usa el recibo de recuperación. Expone import/item/receipt originales, kind y expense efectivos, prueba/revisión y cobertura efectiva. El LEFT JOIN único impide multiplicar membership.

Caso de referencia: conservar header **140 cargos/5 omitidas/3 créditos**, sus 148 recibos y direct-items, los bytes y 140 UUID de gastos. La fuente efectiva pasa a **145 cargos/0 omitidas/3 créditos**, con **148**, nunca 153, miembros. Los cinco gastos nuevos corresponden sólo a cinco cargos reales previamente no creados.

### Owner y contrato de elegibilidad

Nuevo `amex_direct_recovery_service.py` con preview puro sobre bytes retenidos y confirmación transaccional. Finance declara límites corregidos, motivo y confirmación; la UI puede mostrar la cobertura detectada como propuesta, nunca registrarla sin consentimiento. El rango corregido debe contener el original y todas las fechas válidas del archivo; no puede excluir cargos anteriores ni cambiar tarjeta, titular, receptor, moneda o perfil.

Rutas bajo `.../estados/{import_id}/directo/periodo`: GET del formulario y evidencia; POST `/previsualizar` devuelve elegibilidad, diferencias y hash de preview sin escritura de negocio; POST `/confirmar` ejecuta el owner con CSRF, confirmación y versiones. Las tres exigen el acceso Finance y scope del estado; la operación de confirmar comprueba además `amex_direct/corregir_periodo`. Usar bytes privados ya retenidos, no pedir reupload ni aceptar otro archivo como sustituto. Tras confirmar, volver al mismo UUID y URL del estado directo, mostrando declaración original y cobertura efectiva corregida por separado.

Preview vuelve a parsear el original con su parser/perfil y prueba correspondencia completa por hash, row_number y contenido. Sólo admite skipped con motivo exacto de período que el parseo original demuestra como debit válido. Cualquier falta de prueba, tipo desconocido, estado legacy, fuente alterada, recuperación previa incompatible o corte existente bloquea.

Los guards SQL contrastan fecha, moneda, celda/columna, importe firmado, perfil y pertenencia contra `source_json`/`normalized_json` originales inmutables y la fuente retenida; no aceptan como prueba un hash autoconsistente del mismo payload entrante. Requieren exactitud de la economía del Expense efectivo y rechazo de anclas ajenas/reutilizadas. El reparseo de bytes del servicio y los checks materiales SQL se complementan.

Sellado atómico: constraint triggers diferidos en revisión y filas exigen al commit el conjunto exacto de ítems recuperables, conteo esperado, membership digest, conteos efectivos y cardinalidad original completa. Revisión sola o 4/5 filas no pueden persistir. El guard BEFORE INSERT adquiere lock del import, verifica ausencia de corte y pertenencia al conjunto previsto, y rechaza filas adicionales cuando la membresía ya está completa. Una sexta fila o inserción posterior no puede añadir economía sin una revisión nueva (que UNIQUE(import_id) prohíbe en esta entrega).

Confirmación verifica identidad activa y permiso Finance específico `amex_direct/corregir_periodo`, con los mismos alcances de entidad/tarjeta que el estado; denegación explícita prevalece. La acción no otorga otro permiso ni scope. Recibe import, rango, source/version, preview hash, motivo, idempotency key y confirmación.

Locks: file advisory → locks de períodos original/corregido en orden determinista → import/state → lock global de reconocimiento → direct-items/Expense/CFDI/claims afectados en el orden canónico. Nunca adquirir import después del global. No SAT en la transacción. Revalidar fuente, elegibilidad, permisos y ausencia de conflictos bajo locks.

Crear sólo gastos faltantes mediante el expense owner existente; insertar revisión/recuperaciones y eventos, avanzar versión global e invalidar previews anteriores. Cada gasto debe coincidir con importe/fecha/titular/tarjeta/moneda demostrados por el original. Error revierte gastos, receipts y eventos juntos. Conservar todas las evidencias de los 140 cargos existentes: no reescribir sus CFDI, asignaciones, reservas ni consumos.

Para la primera entrega, rechazar recuperación si cualquier fuente del estado ya tiene reconocimiento canónico, aunque no exista un corte directo. No ampliar silenciosamente la reparación a estados con contabilización previa por otro owner.

Los cinco empiezan pendientes del flujo fiscal/presupuestal ordinario. No adquieren CFDI o aprobación por recuperación. Un claim, representación o vínculo contradictorio bloquea la operación completa. Fuentes reconocidas o con corte quedan excluidas de recuperación.

### Integración obligatoria; evitar una corrección sólo de UI

La vista SQL y un resolver Python común deben ser la única definición de kind/ancla efectivos. Conservar ORM físico para escrituras de campos operativos; el resolver retorna explícitamente ítem físico más fuente efectiva. No sombrear atributos ORM ni depender de un proxy que escriba accidentalmente campos físicos inmutables.

Actualizar todos los consumidores económicos, incluso consultas directas fuera de `load_items`: búsqueda por Expense/import; universo de unicidad reversa de matching; consultas de vigencia; uso global de CFDI; contexto/reclasificación; asignación de presupuesto; preview/consumption/representations; snapshots y cortes. Las operaciones sobre el ítem deben leer el Expense efectivo y persistir CFDI/presupuesto/status en el mismo direct-item original.

El hash operativo de los cinco incorpora receipt/hash de recuperación; las fuentes normales conservan la semántica de su hash. El hash global incluye revisión/cobertura efectiva y source_version. No invalidar por ficción la identidad fiscal intacta de los otros 140 cargos; su validez se sigue revalidando canónicamente.

Modificar SQL guards para resolver la misma fuente efectiva: proyección (manteniendo intacta la tupla física original), identidad de Expense, uso global de CFDI, consumo, membership y prueba diferida del corte. Cut membership mantiene 148 miembros, exige resolver toda fuente efectiva y comprueba cuentas/presupuesto/vigencia/pasivo/retenciones/consumos reales. Freeze incluye los Expense recuperados. Prohibir por SQL receipt ajeno, ancla reutilizada, cargo no demostrado y cualquier bypass de la fuente efectiva.

La fecha contable sigue declarada explícitamente por Finance, dentro de la cobertura efectiva confirmada, de edición compatible y período contable abierto; conservar fechas económicas/XML originales. Registrar en el corte tanto declaración original como recuperación efectiva y sus hashes. No mover automáticamente la póliza a agosto o septiembre.

Schema admission exige tablas/vista/guards nuevos mediante contrato/CLI actualizado; runtime no provisiona. La admisión de la vista debe verificar su definición/joins y cardinalidad contractual, no sólo existencia; conservar hashes de funciones/guards. Tras existir recuperación, no operar ese estado con código viejo que sólo lee fuentes físicas. Rollback conserva esquema y datos; bloquea acciones incompatibles o vuelve a código compatible. Down DDL sólo antes de existir receipts y previa comprobación de ausencia de dependencias.

## 5. Mesa de CFDI utilizable — AMEX-QA-02

Nuevo GET scoped `.../estados/{import_id}/directo/cfdi` con `item_id`, `q` literal máximo 120 caracteres, fechas/importe Decimal opcionales y cursor; página 25, máximo 50. Autorizar Finance/conciliar y estado/ítem/contexto efectivo antes de leer catálogo. Filtrar siempre en servidor por receptor del contexto, SAT, tipo I y MXN; validar XML material. Orden estable fecha+ID con NULLS LAST explícito. Cursor opaco emitido por servidor y validado contra contexto de búsqueda en la sesión firmada existente: filtros, import/item/context/version y última fila examinada; no confiar en un blob editable que el cliente pueda reetiquetar. No requiere una credencial/configuración nueva.

La exploración SQL es acotada, con limit+1 y avance desde la última fila examinada aun si se descarta un XML inválido. Devolver `has_more`/truncación explícitos: una página de candidatos vacía no acredita universo agotado si hay más filas por revisar. Un cursor de otro contexto/filtro/versión se rechaza. No guardar XML o contenido de facturas en la sesión; el GET no escribe negocio, aunque pueda actualizar metadatos de navegación en la sesión firmada, como los formularios actuales.

Mostrar UUID fiscal, emisor, fecha, total, vigencia/antigüedad y disponibilidad/conflicto como propuesta; el ID interno sólo es valor oculto de selección. No exponer XML ni identidades de documentos ajenos, receptores arbitrarios o detalles de usos externos. Reusar `_available`/validaciones canónicas; un conflicto compartido se explica sin eliminar la aplicación compartida explícita ya existente. No usar el listado global de cleanup como owner porque no impone este contexto.

La búsqueda por fecha/importe/texto **no confirma** correspondencia. La selección no crea alias comercial ni reemplaza `declared_uuid`. Mantener auto-match estricto aprobado; permitir alias exacto confirmado mediante su owner actual y explicación accesible.

### Seleccionar → observar vigencia → confirmar vínculo

El botón de vigencia actual no consulta una selección sin UUID declarado o alias: añadir POST `.../{import_id}/directo/cfdi/vigencia`, con item, candidate, versiones/hash actuales, request key, CSRF y consentimiento explícito. Extender el owner de status con un universo explícito de **un candidato seleccionado**; no ensanchar silenciosamente el universo de todo el estado.

Reutilizar transporte SAT/receipt/TTL existentes y límites 50/concurrencia2/30s externos; este camino solicita un candidato. Verificar específicamente `amex_direct/vigencia`, incluyendo denegaciones de ver/editar y alcance, antes y después de SOAP; no basta permiso de conciliar. Validar XML/receptor/identidad completa del request fiscal antes de llamar; cerrar transacción antes de SOAP. Guardar sólo observaciones reales ligadas a identidad/hash fiscal y revalidar actor/fuente/contexto/identidad fiscal después. Cambios, cancelación, error o XML distinto no autorizan vínculo ni se convierten en vigente.

Confirmar vínculo usa `link_direct_cfdi_manual`, con motivo, consentimiento, source/item versions y locks/uso global actuales. Receipt vigente fresco se reutiliza; sin él la UI ofrece la consulta anterior y deja la selección pendiente. No eliminar controles de receptor, XML, economía, propina/CFDI compartido o reservas globales. El usuario puede completar estos pasos desde el cargo sin buscar PK internos en otro módulo.

Seleccionar y consultar vigencia no reservan ni vinculan CFDI. La disponibilidad mostrada es provisional: confirmar vuelve a comprobar `conciliar` separadamente y el uso global bajo locks. Una factura que se ocupa entre búsqueda y confirmación produce conflicto, no un segundo uso.

GET no hace SAT, DDL ni escritura. No se añaden credenciales, descargas ni jobs. Nuevas rutas requieren inventario/matriz/hash actualizados y pruebas de denegación.

## 6. Clasificación, pendientes y propuesta contable — QA-03 a QA-06

- Un selector visible «Proyecto / torneo» conserva los IDs/códigos existentes y alimenta los campos excluyentes `tournament_id`/`project_code` actuales. Dirección General sigue siendo su registro autorizado, no un torneo deportivo adicional. Filtrar BudgetConcept por relación exacta; deshabilitar sin proyecto y limpiar categoría al cambiar contexto. Backend vuelve a validar pertenencia/actividad/dirección/edición; JavaScript no concede autoridad.
- Separar formularios no anidados de Guardar y Enviar, compartiendo selección y versiones actuales mediante UI. Guardar valida clasificación sin consentimiento de envío. Enviar sólo selección persistida válida, con su propia confirmación y transición existente. No fusionar los dos hechos.
- Mostrar todas las excepciones del preview, responsable y siguiente acción. Añadir issues estructurados por ítem cuando la información actual sólo sea global, conservando `exceptions`/`reason` para consumidores existentes. No presentar bloqueos faltantes como resueltos.
- Mostrar propuesta canónica de cuentas, conceptos, Debe/Haber y totales exactos antes de confirmar; si incompleta, indicarlo. Usar `progress.lines` del owner, nunca calcular IVA o reconstruir journal en HTML. Tras corte, mostrar snapshot congelado y enlaces de descarga; no cuentas actuales como sustituto.
- Cola CP: excluir reconocidos y fuentes congeladas de acciones. Pendientes `sent` permiten asignación; `assigned` sin corte pueden mostrarse con su partida y sólo acciones previas legítimas (por ejemplo devolución), sin botón de reasignar que el owner rechaza. No cambiar estados contables ni asignaciones para limpiar la lista. Conservar historia bajo el mismo scope.
- Mantener estilo compartido, tabla manejable, selección/conteos visibles y filtros operativos sin esconder filas no resueltas del corte. Pago de tarjeta conserva owner y enlace actuales; sin nuevo gate fiscal para pagar.

## 7. Archivos previstos y dependencia

Modificar de forma acotada `amex_cut_export_service.py`; `admin_amex_direct_routes.py`; `amex_direct_{intake,common,context,matching,status,budget,accounting}_service.py`; `amex_direct_models.py`; admisión/contrato/CLI de esquema; definición de permisos/rutas cuando sea necesaria para mapear las nuevas acciones al guard existente.

Añadir `amex_direct_recovery_service.py`, un módulo común de fuentes efectivas y un owner de búsqueda CFDI si separarlo evita ampliar matching. Añadir la migración de recuperación y los modelos correspondientes. Actualizar sólo consumidores económicos enumerados que realmente consulten anclas físicas, identificados con inventario antes de build; una dependencia nueva fuera de esos owners exige revisión de alcance, no parche silencioso.

Tests: unit/gastos de rutas, export/cut, opciones/contexto y admisión; PostgreSQL de esquema/backend/budget/recognition y aceptación AMEX; browser directo. Documentar evidencia/build report y regenerar ruta inventory/matrix donde corresponda. Sin nuevas dependencias productivas, cron ni servicios externos.

## 8. Plan de aceptación y validación

1. Repro export dos cargos rojo→verde en XLSX/CSV/ZIP; negativos de multiplicidad/cuentas/importes/evidencia. Regresión INFORME de snapshot congelado.
2. Preview de `activity.csv` íntegro y septiembre muestra cinco filas 31/agosto y no permite nueva persistencia incompatible; POST manipulado también rechaza con cero filas/Expense creados.
3. Recovery en PostgreSQL owner real: originales/140 anchors byte- e identidad-equivalentes; cinco nuevos; 148 miembros efectivos; replay/concurrencia sin huérfanos; stale/hash/parser/signo/receipt ajeno/conflicto/reconocimiento previo/corte rechazan atómicamente. SQL directo de revisión sola, 4/5 filas, sexta fila y economía/Expense falsificados falla al commit sin efectos. Probar vista y admisión ante definición alterada.
4. Paridad resolver Python/vista SQL; recuperados incluidos en reverse uniqueness, uso fiscal compartido, contextos, presupuestos, retenciones, freeze y prueba completa de corte. Omitir cualquiera bloquea SQL al commit.
5. Buscar sin acceso/contexto/receiver válido deniega; paginación/escaping/fechas/Decimal/cursor, páginas vacías con más filas inválidas, fechas nulas; ningún GET externo/escritura de negocio. Selección sin alias consigue observación SAT por transporte simulado y sólo después se vincula; fuente/XML/permiso que cambia durante consulta bloquea. Cubrir conciliar permitido/vigencia denegada y viceversa, y revocación durante SOAP. Fresh receipt, cancelado, stale y uso concurrente cubiertos.
6. Chromium: Dirección General sólo categorías propias; Guardar sin envío; Enviar con confirmación; todos los bloqueos; propuesta Debe/Haber y actor CP separado. Sin mocks de servicios internos de negocio en la aceptación integral.
7. Flujo completo del archivo original 148 filas, dependencias fiscales/catálogos declaradas sintéticas, recuperación del período, 145 cargos y 3 créditos tratados explícitamente → una póliza balanceada → XLSX/CSV/ZIP descargados e inspeccionados: cobertura exacta, RFC limpio, impuestos/pasivo, replay y export freeze. Sin CSV, XML real o credenciales en Git. No llamar ese resultado póliza productiva ni UAT real.
8. Scoped lint/type/format, inventario y schema checks; aceptación independiente, luego validador read-only contra historia/spec/diff. Corregir fallos atribuibles al cambio y repetir checks afectados. Enumerar pendientes/infrastructura/deuda previa sin esconderlos.

El assessment sólo cambiará de dictamen al cubrir sus criterios; generar la póliza interna sin descargar un archivo válido vuelve a fallar.

## 9. Canon: textos propuestos para revisión humana

No se editaron los canons al preparar esta propuesta. Las correcciones de exportación/selector/cola restauran el contrato ya aprobado. La recuperación introduce fuente efectiva adicional y requiere aprobar estos textos antes de actualizar producto/ingeniería y sus hashes, en el mismo PR:

**Producto, enmienda de 2026-10-15 a AMEX directo:** «Contabilidad puede corregir explícitamente la cobertura temporal declarada de un estado directo todavía no reconocido cuando el archivo original demuestra cargos omitidos únicamente por estar fuera del período capturado. La recuperación preserva el original y sus recibos, conserva los cargos ya creados y agrega sólo los cargos faltantes mediante evidencia auditada e idempotente. No cambia identidad fiscal, no crea informes, no aprueba gastos ni modifica fuentes reconocidas. La fecha contable sigue siendo una declaración explícita dentro de la cobertura efectiva confirmada y un período abierto.»

**Ingeniería, enmienda de 2026-10-15 a AMEX directo:** «La recuperación acotada de período utiliza revisiones y recibos append-only vinculados a las filas originales omitidas, con una única relación de fuente efectiva compartida por Python y guards SQL. Los headers, originales, membresía física y anclas existentes permanecen inmutables; sólo se crean anclas para cargos demostrados antes no importados. Matching, vigencia, presupuesto, reconocimiento, unicidad fiscal y freeze resuelven la misma fuente efectiva. El corte conserva cobertura total y evidencia de la revisión. Versiones, locks y constraints rechazan correcciones concurrentes incompatibles, fuentes ajenas, doble reconocimiento y recuperación después de un corte. El esquema se instala mediante migración owner-run explícita y no permite downgrade operativo a código que ignore recuperaciones existentes.»

Sweep sin cambios: no se propone un nuevo inventario general. Registrar aprobación, fecha y razón de estas enmiendas; actualizar hashes sólo después de revisión humana. No automatizar aprobación de canon.

## 10. Promoción y riesgos pendientes

Riesgo principal: omitir un consumidor/guard de ancla efectiva y producir una visión parcial; se exige inventario y paridad SQL/Python. El buscador es superficie fiscal: scope antes de consulta y validación bajo locks son obligatorios. El fallback de comparación del exportador debe conservar multiplicidad y validación COI.

Antes de producción: diff final/build report aprobados, migración preflight con backup verificable, aplicación owner-run autorizada, release compatible y comprobación de runtime. La aplicación de recuperación al estado real requiere preview actual, límites declarados y confirmación del operador autorizado; no se ejecuta desde un script sin decisión de negocio. Las decisiones de CFDI, clasificación y partida no se completan con los fixtures.

La migración cambia hashes de guards: el código anterior puede fallar su admisión incluso antes de recuperar filas. Planificar una ventana coordinada de pausa de los procesos que ejecuten AMEX, migración/reinicio del release compatible y verificación de su CLI antes de habilitar acciones; no workers mixtos ni promesa de cambio sin interrupción. Identificar procesos afectados en preflight, sin detener servicios ajenos. La ventana y sus efectos requieren autorización de promoción posterior.

Rollback de código nunca borra fuentes ni recuperación/cortes. No downgrade a código incompatible con fuentes recuperadas; congelar acciones afectadas manteniendo lectura/exportación compatible. Sin dato de recuperación, rollback vacío puede revertir esquema sólo con autorización y pruebas de dependencias. Los 14 históricos y los estados legacy permanecen fuera de alcance.


## Continuidad de aprobación

El usuario aprobó esta especificación y los dos textos exactos de canon después de dos revisiones read-only del diseño sin hallazgos importantes pendientes. Huella del documento presentado: `75dd45c04961e089f47e5aa49efbe1d0b9d551f893689c865473d01b99c2197d`. Se autoriza implementar y probar el alcance descrito; las autorizaciones de producción/publicación mantienen sus etapas separadas.
