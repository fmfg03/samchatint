# AMEX: sugerencias por cargo, IVA y cancelación de cuenta de gastos

Fecha:2026-10-20. Estado: SPEC_APPROVED. Aprobada explícitamente por el usuario, incluidos los textos exactos de canon de§10, mediante «aprobada» el2026-10-20.
Historia aprobada: docs/roadmap/amex-suggestions-settlement-20261020-story.md.
Esta aprobación autoriza implementación y pruebas aisladas; no autoriza efectos productivos. Ninguna póliza o vínculo real se creó al prepararla.

## 1. Baseline y evidencia

Runtime web copa_telmex_dashboard.py, servicio samchat-gastos.service. Release observado y origin/main coinciden en0df07ace2b13e942c322b30f88a4c5b811b85e62. Baseline limpio consultado: /root/samchat/.worktrees/pr487-finish-20261016. Bootstrap de los tres canons en orden, hashes coinciden con register: producto3f92bc66…, ingeniería b98ba82d…, sweep2c55cc80….

El checkout principal contiene cambios y archivos privados ajenos; no se utilizará para construir. La implementación multiempresa no publicada en .worktrees/multiempresa-20261014 toca owners comunes y constituye un frente separado. No copiar sus cambios, conceder permisos ni mezclar sus migraciones para cerrar esta historia. Antes de integrar/publicar se reconciliarán versiones efectivamente compartidas mediante revisión explícita de diff; si cambia la autoridad o arquitectura se revisará sólo esa porción.

Hallazgos:
- amex_cfdi_matching_service.py tiene ranking legacy, pero sus entradas rechazan is_direct_amex_expense. No retirar esa protección para reutilizarlo.
- amex_direct_cfdi_search_service.search_direct_cfdis autoriza actor/estado/fuente, filtra entidad SAT/MXN/tipoI y comprueba XML/uso/receipt; hoy no ofrece semejanza ni muestra método de pago.
- amex_direct_matching_service.eligible_candidates exige UUID declarado o identidad comercial confirmada; usa importe exacto, fecha±3d para alias y receipt vigente. No relajar automáticamente esos criterios por la puntuación de sugerencias.
- covered_source_proof y guards del corte sólo admiten reconocimiento canónico. report_bridge/asociación bancaria no son pólizas de cancelación.
- fiscal_lines y _single_expense_fiscal_lines reutilizan build_expense_accounting_preview para cuentas/impuestos. En el recorrido DIRECT inspeccionado no se encontró un requisito explícito de REP. Se debe probar la política para PUE y PPD, no agregar una falsa liberación de un gate inexistente.
- El auxiliar de deudores usa DEBTOR_ORIGINS y cálculos de presentación float; no sirve como fuente exacta de un importe cancelable por línea.

## 2. Enfoque y fases de construcción

A. Sugerencias de solo lectura, contratos y ranking determinista; luego UI de lista/detalle.
B. Evidencia PUE/PPD/complemento y verificación de IVA para todas las tarjetas de Plataforma Sports.
C. Cobertura previa y cancelación de cuenta de gastos con recibos contables y SQL. A y B no se presentarán como cierre de la historia mientras C esté pendiente.
D. Aceptación PostgreSQL/navegador, revisión independiente y reporte final. Publicación/migración/deploy posteriores requieren su autorización correspondiente.

No nuevas dependencias, motor IA externo, jobs o consultas SAT disparadas por GET. La sugerencia es una lectura del catálogo descargado; no descarga facturas, crea alias ni consume/reserva CFDI.

## 3. Sugerencias: autorización, ranking y recorrido

Nuevo owner de lectura amex_direct_suggestion_service.py. Reusar load_statement/load_items/effective_source_for_item y los validadores de identidad XML del owner actual; evitar segunda implementación de autorización o disponibilidad fiscal. Una sugerencia nunca crea una representación de identidad contable.

Universo: sólo entidad/receptor del contexto fiscal autorizado, origen SAT, tipoI, monedaMXN y total positivo válido. No otra compañía ni acceso por nombre del empleado. Usuarios inactivos/denegados o sin ámbito se rechazan antes de consultar facturas. Alcance de superadmin sigue siendo explícito.

Sin corte obligatorio por mes, año o distancia de fechas. La fecha influye en orden y explicación; no descarta por sí sola. La búsqueda manual mantiene filtros opcionales explícitos. Ninguna fecha se sustituye por fecha de carga; una ausente se muestra como dato pendiente.

Ranking propuesto, versionado suggestions_v1, sólo para ordenar y sin probabilidad de certeza:
1. UUID explícito o RFC comercial confirmado concordante, cuando esa evidencia exista.
2. Importe exacto al centavo, sin tolerancia oculta de1peso.
3. Similitud de comercio (tokens normalizados del descriptor frente a emisor/RFC/descripción fiscal).
4. Menor diferencia relativa de importe, con Decimal y diferencia firmada visible.
5. Menor distancia de fechas; desempate estable por UUID.

La identidad comercial exacta y la comparación monetaria conservan sus evidencias separadas. Una coincidencia de texto o una cifra exacta nunca acredita por sí sola correspondencia. El total, fechas y método de pago mostrados se verifican contra el XML canónico al confirmar, no se confía en el HTML.

Implementación acotada en PostgreSQL con funciones incorporadas de texto/números, sin instalar extensiones: obtener los mejores candidatos por cargo mediante consulta set-based parametrizada y orden estable antes de LIMIT. Las normalizaciones y claves de orden tendrán pruebas de paridad SQL/Python. No tomar los primeros CFDI por fecha y llamarlos los mejores. No limitar silenciosamente a un primer lote del catálogo. La selección final se valida con identidad XML; páginas de candidatas inválidas avanzan con cursor opaco y mensaje de cobertura incompleta, nunca «no hay facturas» si no se terminó la búsqueda autorizada.

Página de25cargos; máximo3candidatas visibles por cargo inicialmente y acceso a más candidatas. Ranking del universo y lecturas de permisos/config/contexto en lote; evitar una descarga SAT por celda. Disponibilidad/receipt son lecturas canónicas de las candidatas devueltas y quedan etiquetadas provisionalmente. Candidatas en uso muestran advertencia sin revelar documentos ajenos y no se vinculan como si fueran libres. No se agregan límites de similitud que oculten búsquedas lejanas sin explicarlos.

UI: mismas clases/estructura visual del módulo. Cargos mantienen selección, abonos y pendientes. Cada tarjeta de sugerencia muestra emisor/RFC/UUID, total, fecha fiscal, fecha bancaria, deltaimporte, deltadías, PUE/PPD, vigencia y uso provisional. Estados: sugerencias disponibles, varias posibles, pendiente de datos o sin candidatas en búsqueda completa. No presentar automáticamente la primera como confirmada. Buscar manualmente sigue disponible.

GET existentes de lista/detalle/buscador integran lectura de sugerencias, sin nuevos POST de escritura. Cambios de filtros/estado/fuente/contexto invalidan cursores. La confirmación utiliza POST vincular existente con consentimiento/motivo, CSRF, source/item/context version, XMLhash y disponibilidad actual. Revalidar bajo locks y registrar recibo del criterio mostrado. No autollenar propina ni aprobar uso compartido. El preview fiscal compartido hoy invoca _calculate_amex_tip_amount cuando propina explícita es0; añadir un modo estricto interno para DIRECT que desactive ese fallback sin cambiar el default de los otros callers. La fórmula monetaria/fiscal canónica sigue siendo compartida; el modo estricto rechaza diferencias no explicadas y conserva propina sólo cuando fue declarada y probada por el owner. El automático estricto existente conserva contrato; coincidencias aproximadas requieren confirmación.

## 4. PUE/PPD, fechas, complemento e IVA

Conservar fecha original del cargo, fecha de emisión del CFDI y fechas de pago realmente evidenciadas como datos distintos. Fecha contable continúa siendo explícita y de período abierto bajo reglas actuales. No mover automáticamente la póliza a octubre por obtener el XML en octubre, ni reabrir septiembre, ni crear asiento transitorio por defecto; esa automatización no fue parte de la historia aprobada.

Mostrar PUE/PPD leído de XML canónico. Método ausente/contradictorio no se inventa; se informa como evidencia pendiente para revisión. Una operación PPD puede haber sido liquidada mediante la tarjeta aunque el complemento no esté disponible. Nunca crear dos pasivos por interpretar PPD como deuda todavía abierta cuando existe pago AMEX probado.

Complemento: lector local limitado al mismo receptor/emisor y relaciones UUID explícitas del XML tipoP; sin enlace por importe/nombre solamente. Mostrar «Disponible», «No localizado en las facturas descargadas» o «No verificable». Falta de catálogo/parseo no equivale a afirmar inexistencia. No descargar ni consultar SAT sin consentimiento. Su ausencia no es bloqueador de matching/IVA/corte/exportación.

Política operativa solicitada: todas las tarjetas AMEX de Plataforma Sports en su ámbito fiscal configurado, sin excepciones por titular. No extenderla automáticamente a otras empresas o a módulos no AMEX. PUE y PPD, con/sin REP, reutilizan importes XML y los mappings fiscales vigentes. Nunca calcular IVA como total/1.16, fabricar IVA0 ante fallo ni cambiar requisitos de deducibilidad/retenciones/prorrateo existentes. El preview común tiene fallback de IVA estimado sin trasladosXML; en DIRECT el modo estricto debe exigir desglose fiscal válido del XML antes del corte, mostrando pendiente si falta. No transformar ausencia de desglose en IVA0. Diferenciar un XML legítimamente exento/no objeto o con IVA0 demostrado de un XML incompleto/inconsistente: el cero fiscal respaldado sigue siendo válido y no exige un traslado positivo. Propagar el flag interno con defaults anteriores preservados por build_expense_accounting_preview→_single_expense_fiscal_lines→fiscal_lines y sus callersDIRECT, sin duplicar fórmulas. La propuesta contable muestra cuenta e importeIVA reales. Política y evidencia de complemento se congelan en snapshot versionado; no marcar globalmente un CFDI como cumplido ni modificar XML/pólizas ya reconocidas.

Si el owner actual ya registra el IVA sin REP, preservar comportamiento y agregar evidencia/pruebas. Cualquier bloqueo distinto demostrado durante build se atribuye al cambio o a deuda previa antes de ampliar archivos. Esta política no autoriza reescribir declaraciones DIOT o períodos históricos.

## 5. Tres rutas contables y evidencia previa

R1 Gasto nuevo: clasificación Contabilidad→partida CP→débito de gasto e impuestos canónicos/abono pasivo de tarjeta. Conserva una póliza inicial íntegra por estado, cuentas activas, período y freeze existentes.

R2 PasivoAMEX ya reconocido: demostrar la identidad bancaria y consumo/corte/póliza previa, cuenta de tarjeta, entidad, moneda e importe. Asociación canónica a reconocimiento existente, sin crear gasto/IVA/presupuesto ni segundo pasivo. Adaptar el lector de cobertura para probar report-first mediante asociación explícita sin considerar posteable al header report_bridge. No admitir identidad por coincidencia de nombre/CFDI/importe. Estado con sólo R2 no genera póliza vacía. Un owner directo crea asociaciones propias con recibos previos íntegros; el binder legacy conserva su prohibición de recibir fuentes DIRECT. La identidad bancaria única y la referencia al consumo/corte/póliza originales quedan probadas físicamente.

R3 Cuenta de gastos contabilizada que aún tiene crédito cancelable: Contabilidad selecciona origen explícito en el detalle del cargo. Leer póliza/líneas originales del reconocimiento real, con cuentaID/código originales, cuenta-de-gastos/documento/expense y evidencia de aprobación. No resolver la cuenta histórica por el nombre de Odilón ni por su mapping actual.

La fuente se prueba por pólizaID+líneaID+expenseID+documento/cuenta-de-gastos y cuenta históricaID/código de la línea, no sólo por metadata declarada. La cancelación requiere crédito REAL de origen pendiente: al confirmar, débito a esa cuenta por el importe cancelado y crédito al pasivo de la tarjeta. Un débito de anticipo o saldo auxiliar agregado no es un crédito cancelable. Usar Decimal a partir de importes exactos del ledger y asignaciones; no auxiliarfloat ni total del informe. Si falta atribución por línea/gasto, cuenta o saldo, mantener excepción con causa; no inventar particiones. Reversiones y otras liquidaciones sólo se descuentan con vínculo y evidencia canónica; si no es posible demostrar crédito residual libre, el caso queda pendiente.

Importe cancelado debe corresponder al cargo y no superar crédito residual demostrado. Cuentas/moneda/entidad contradictorias, saldo en sentido equivocado, reconocimiento previo de pasivo o consumo competitivo bloquean sólo ese caso y, por atomicidad, su corte completo. No cancelar otra vez un saldo ya utilizado. No generar reconocimiento de presupuesto/gasto/IVA en R3 ni reenviar informe aprobado a CP. El CFDI ya utilizado se referencia desde el reconocimiento fiscal original: no volver a vincularlo como gasto nuevo ni rechazar automáticamente el caso sólo por ese uso legítimo. El bloqueo de uso global de terceros consumos permanece intacto. Los informes de Odilón siguen fuera de las colas directas según la política actual; cargos del banco visibles.

Todo estado conserva las líneas originales, omisiones/recuperaciones y abonos existentes. El corte completo puede incluir R1+R3 y referenciar R2 sin nueva contabilización. R3 usa coverage_kind='settlement', separado de 'new' y 'prior': su proof son las asignaciones a créditos históricos y su póliza de cancelación; no llama fiscal_lines ni exige una nueva partida presupuestal o aplica IVA de nuevo. Un estado sóloR3 produce una póliza balanceada de cancelación; el gate anterior de «al menos un gasto nuevo» se adapta a «al menos una línea contable nueva R1/R3», sin permitir cortes0 o parciales.

R2 exclusivamente se finaliza mediante un recibo no contable separado del corte posteado. Guarda snapshot completo de todas las filas, asociaciones y fuentes previas, actor/fecha/sourcekey, decisión e idempotencia. No modificar accounting_poliza_id aNULL en cortes históricos ni fabricar un corte con póliza0. Preview muestra «Todos los cargos ya reconocidos» y confirmación explícita genera sólo ese recibo; posteriores lecturas muestran las pólizas previas. Exports contables siguen disponibles desde sus cortes originales, sin export nuevo vacío.

### Entradas públicas para la cuenta de gastos

Añadir GET /admin/contabilidad/amex/estados/{import_id}/directo/cuenta-gastos?item_id=…: lista acotada de créditos de origen que el actor puede revisar en el ámbito del estado. Requiere Finance activo, permiso conciliar, contexto/entidad/tarjeta autorizados y lectura del origen real; no habilita un explorador global de empleados o pólizas. No selecciona por nombre/importe únicamente.

Añadir POST /admin/contabilidad/amex/estados/{import_id}/directo/cuenta-gastos para seleccionar o sustituir una propuesta antes del freeze. Acepta itemID, póliza/línea/expense/documento/cuentaID origen, source/itemversions, motivo y confirmaciónCSRF. Verifica fuentes reales, registra evento de selección versionado y cambia sourceversion; no crea asignaciones finales, póliza, consumo ni saldo. Un pendiente o payload ajeno se rechaza sin escrituras parciales. R2 con representación ya demostrada se deriva de la lectura; si necesita selección explícita usa la misma entrada acotada. POST confirmar existente revalida y materializa R1/R2/R3 con permiso confirmar. La selección no concede permiso confirmar ni evita el consentimientofinal.

## 6. Persistencia y migración propuesta

Sugerencias se calculan en lectura; no tabla de ranking ni escritura de preferencias en GET. Las decisiones financieras conservan eventos/hashes/fuentes del owner.

R3 necesita recibo durable: nueva tabla append-only amex_expense_account_settlements y su tabla hija amex_expense_account_settlement_allocations para asignaciones a líneas originales; FK al cargo/directitem/import/entidad/tarjeta, cuenta-de-gastos/documento/expense, póliza y línea de origen, cuenta contable histórica, monto/moneda, actor/motivo/sourcehash, póliza y líneas destino/corte. Clave idempotente por cargo y decisión. Cabecera de selección revocable antes de confirmar vive en evento/version del item; asignación final sólo se inserta con la póliza/corte atómicos.

Constraint/trigger diferidos validan fuente/receptor/tarjeta, referencia exacta al crédito original, totalasignado=importecargo, no exceso de crédito residual y correspondencia con débito/credito reales de destino. Rechazar UPDATE/DELETE de evidencia final; FK reales y checks de importe positivo/moneda. La inclusión/exclusión de anulaciones se deriva de asientos canónicos de reversión; si no hay evidencia unívoca bloquear, no inferir saldo.

R2: tabla append-only amex_direct_prior_coverages con FK a directitem/import, consumo y representación originales, corte/póliza originales, identidad bancaria y contexto/importe/moneda probados. Cada asociación final pertenece exactamente a un corte nuevo mixto o a un recibo no contable; checks/FKs/guards prueban esa relación, no un JSON cliente. Nuevo recibo amex_direct_completion_receipts para estados íntegramente R2, único por importación, con identidad/sourcekey/version/actor/fecha/idempotencia/snapshot inmutable y miembros exactos referidos por amex_direct_prior_coverages. Creación y membresía son atómicas y verificadas por constraint diferido. No permite un corte posteado competitivo para el mismo import ni completar estados con filas sin resolver.

Extender explícitamente contrato/guards de cobertura DIRECT para R2/R3: no introducir report_bridge como un consumo contabilizable ni quitar exclusión legacy/direct. Los guards distinguen 'new' (gasto fiscal/presupuesto), 'settlement' (asignaciónledger/contrapartidaAMEX) y 'prior' (sin líneas nuevas); validan membresía, actividad contable nueva, cuentas/importes de débito-crédito, snapshot/export y cobertura total según ese tipo, sin relajar las otras ramas. Identidad bancaria continúa siendo única; un origen compartido puede recibir cancelaciones sólo hasta su residual, una fila/cargo no se consume dos veces. Concurrencia se cierra con locks por líneas y constraints, no comprobación Python aislada.

Nueva migración forward owner-run y rollback que rehúsa revertir cuando hay evidencia final. Refrescar metadata-contract/hashes del CLI de esquema y pruebas PG. Ningún ensure-schema/DDL de arranque, backfill o aplicación productiva en este trabajo. Integrar nuevo origen en DEBTOR_ORIGINS/readers pertinentes para que el auxiliar refleje cancelación una vez, sin sumar nuevamente gasto/IVA/pasivo desde el movimiento de banco. No tratar la propuesta de selección como asiento real.

Esta especificación aprueba el modelo de cabecera/asignación y sus invariantes, no nombres o columnas ajenos a las fuentes verificadas. El DDL final debe mapear cada FK a su tabla real y recibir revisión independiente antes de aprobación final. Si una fuente necesaria no existe, reportar excepción; no crear saldos históricos de apertura.

## 7. Transacción, seguridad y exportación

Reusar frontera UOW, permisos y orden de locks existentes. Ninguna llamada SAT dentro de locks financieros. Antes de build documentar el grafo real import/contexto→lockglobalAMEX→fuentes de crédito ordenadas→ledger y dependencias del presupuesto/CFDI; contrastarlo con owners de deudores/reversión. No conectar R3 si introduce un ciclo; resolver sólo la porción afectada bajo revisión.

En confirmación revalidar fuente/actor/config/XML/evidencia original y saldo bajo locks. Póliza, asignaciones, consumo bancario y corte deben confirmarse o revertirse juntos. Guard tardío debe revertir toda la operación aunque caller capture la excepción y haga commit. Retry mismohash devuelve mismo recibo; payload distinto stale/conflict. Nuevas reglas no modifican decisiones/cortes congelados.

Preview: no DML, DDL, autoflush, locks ni SAT; propuesta balanceada con cuentas y destino explicados. Exportar XLSX/CSV/ZIP desde snapshot congelado e incluir líneasR3 y UUID de evidencia en formatos existentes, sin recalcular gasto histórico. Conservar RFC limpio en XLSX. No emitir nuevamente las pólizas originales.

Pago de tarjeta usa owners/pantalla actuales; solicitud y pago real son distintos. Débito pasivo/credito banco sólo con evidencia de pago. No nuevo gate de REP/CFDI para pagar ni automático de monto del estado.

## 8. Archivos previstos

Runtime existente (sólo hunks necesarios):
- src/devnous/gastos/services/amex_direct_cfdi_search_service.py
- src/devnous/gastos/services/amex_direct_matching_service.py
- src/devnous/gastos/services/amex_direct_accounting_service.py
- src/devnous/gastos/services/amex_direct_common.py y amex_direct_effective_source_service.py si requieren exponer datos ya poseídos; no cambiar autoridad/fuentes.
- src/devnous/gastos/routes/admin_amex_direct_routes.py
- src/devnous/gastos/services/amex_recognition_service.py y amex_accounting_cut_service.py para prueba/cobertura nueva, conservando fórmulas.
- src/devnous/gastos/services/expense_accounting_service.py y amex_accounting_posting_service.py: sólo modo estricto interno DIRECT para no inferir propina/IVA; defaults de consumidores anteriores intactos.
- src/devnous/gastos/services/employee_debtor_accounting_service.py para origen y lectura del auxiliar, sin alterar posting de informes ordinarios.
- src/devnous/gastos/amex_direct_models.py y models.py para modelos/registro de nuevas tablas.
- src/devnous/gastos/services/amex_direct_schema_contract.py y schema-policy relacionado, scripts/check_amex_direct_schema.py.

Nuevos: amex_direct_suggestion_service.py, amex_expense_account_settlement_service.py, módulo de modelos de settlement si se separa del actual, migración forward/rollback20261020_amex_expense_account_settlements.sql. Refrescar registro/inventario/matrix y hashes por GET/POST cuenta-gastos de§5; conservar las acciones existentes conciliar/confirmar y su control de ámbito, sin nuevas concesiones implícitas. Mantener POSTcanonical vincular/confirmar.

Pruebas: unitarias de ranking/búsqueda/IVA/settlement/routes; integración PG de settlement/cobertura/constraints/concurrencia y regresiones de recognition; browser del estado completo; aceptación del recorrido y exports. Docs de historia/spec/buildreport y canons/register indicados en§10.

Cambios en servicios fiscales compartidos sólo si se demuestra dependencia directa del alcance; una política global nueva fuera AMEX no está autorizada. No tocar checkout sucio ni publicar multiempresa incompleto.

## 9. Aceptación y comandos

AC1–3: ranking por importe/proveedor/fecha con razoness, universo de otra entidad fuera, mismafactura candidata de dos cargos sin reserva, datosfaltantes/paginación/escapeSQL/HTML/permisos. CasoSuntory3814, septiembre29→octubre6; también año distinto y fecha lejana sin descarte sólo por fecha. Diferencias monetarias visibles, nunca propina inferida ni siquiera por fallback del preview común. Probar restaurantecon delta y propina explícita0 rechaza/requiere revisión, propina declarada legítima sí se conserva, y callerslegacy mantienen su default.
AC4–5: matriz PUE/PPD×REPpresente/ausente/noverificable×tarjetas configuradas de Plataforma. Mismos importesIVA/cuentas desdeXML; sin gateREP. Otraempresa sin heredar política. XMLcambiado/cancelado/noidentidad, mappingIVAausente y parcialidad/IVAcompartido conservan guards. XML incompleto/inconsistente no genera IVA estimado ni cero ficticio enDIRECT; XML exento/noobjeto/IVA0 legítimamente demostrado sí pasa. Defaultlegacy intacto.
AC6: Contabilidad proyecto/categoría, CP sólo línea y mapping canónico, sin duplicar asignaciones; presentación homogénea.
AC7–8: ownersreales producen primero comprobación/ledger; después cargo y cancelación. Verificar saldo original y líneas físicas de salida, cerogasto/IVA/budget adicional, pasivo una sola vez y lecturaauxiliarcorrecta. Origen sin asiento, débitoanticipo, cuentaactual distinta, saldoagotado, conflictoR2, entidadajena, concurrentes, rollbacktardío y replay. Corte sóloR3, mixtoR1/R2/R3, sóloR2 sinpóliza0, freeze y exports exactos.
AC9: regresión pago pasivo/bancos, no nuevo gasto. UAT real se ejecutará con sesión autorizada; fixture sintética no prueba correspondencia real o aceptación de Contabilidad.

Usar .venv/bin/python -m pytest con paths/nodos focales; PG serial sobre base desechable con DDL/triggers activos y lease coordinada; Playwright/Chromium en entorno aislado. No reutilizar base productiva para tests. Ejecutar black --check, isort --check-only, flake8, mypy focales; registrar deuda heredada por separado. Cobertura nueva/modificada>=85%, gates de rutas/inventario/canon y seguridad de repo. No asumir esos checks aprobados: todavía no ejecutados para esta propuesta.

Aceptar sólo con mapeoAC→resultados, pruebas PostgreSQL efectivas para cancelación y dos recorridos navegador (gasto nuevo y cuenta-de-gastos), revisión independiente readonly y reporte de archivos/comandos/resultados/fallos/desviaciones/riesgos. Sin bloquear envío de propuestas por REP ausente.

## 10. Canon: textos exactos propuestos para revisión humana

Se requiere enmienda de producto e ingeniería por nueva política explícita de IVA y representación de cancelación real. No se editaron los canons. La aprobación técnica solicitada incluye la revisión de estos textos concretos; hashes se actualizan sólo después de aprobarlos. Sweep: Canon unchanged, inventario histórico sin nueva observación productiva.

Producto, apéndiceAMEX, fecha2026-10-20:
«AMEX directo presenta sugerencias de CFDI descargados de la entidad autorizada según evidencia de importe, comercio y fechas; distancia de fechas o cambio de mes no descartan por sí solos una candidata. La semejanza es propuesta y no confirma identidad, propina, uso compartido ni contabilización. PUE/PPD, fecha del cargo y emisión fiscal se conservan diferenciados. Conforme a la política operativa indicada por Plataforma Sports para todas sus tarjetas AMEX, la ausencia del complemento de pago se informa y no bloquea conciliación, registro del IVA acreditable desde el CFDI ni póliza; no altera requisitos restantes ni reescribe historia. Un gasto nuevo se clasifica/presupuesta; un pasivo AMEX ya reconocido sólo se asocia; un crédito real pendiente en cuenta de gastos se cancela contra AMEX mediante evidencia de origen y saldo, sin nuevo gasto, IVA o presupuesto. Ninguna de estas acciones registra el pago bancario de la tarjeta.»

Ingeniería, apéndiceAMEX, fecha2026-10-20:
«Sugerencias DIRECT son lecturas fiscalmente acotadas y explicables, sin descarga, reserva o asignación automática por semejanza. Confirmación revalida fuente/versiones, XML, autoridad, vigencia y uso mediante los owners canónicos. Corte versionado conserva método de pago, evidencia de complemento y política aplicada a las tarjetas autorizadas de Plataforma Sports. Cancelación de cuenta de gastos exige asignaciones append-only a créditos reales de líneas contables históricas con identidad y residual exactos; no usa saldo auxiliar agregado ni cuenta inferida por nombre. Póliza, cancelaciones, cobertura bancaria y corte comparten transacción y constraints de unicidad/noexceso; pasivo ya reconocido no se genera de nuevo. Guards y esquema sólo cambian mediante migración owner-run explícita y metadata-contract verificado. Los snapshots reconocidos se exportan inmutables y una asociación report_bridge nunca se vuelve asiento por sí sola.»

Razón/evidencia: story aprobada en conversación del2026-10-20; esta spec y sus pruebas futuras. Estado de enmienda: propuesta, sin implementación/publicación/migración/deploy ni aceptación financiera.

## 11. Rollout y recuperación

Promover un commit integrado revisado, con respaldo privado verificado, pausa de callers afectados, migración owner-run, nuevoCLIready, runtimecompatible y pruebas de salud/guards/rutas. Conservar configuración y bundles. No aplicar nuevos scripts a producción en fase de spec. Tras recibosR3 o snapshots con nueva semántica, no downgrade a código que ignore settlement; recuperar hacia adelante. Rollback de esquema sólo vacío y explícitamente autorizado; nunca borrar eventos/pólizas para volver a verde.

Riesgos principales: cancelar crédito equivocado; duplicarAMEX ya reconocido; N+1/rankingtruncado; concurrencia/reversión de origen; ambigüedad de entidad/permisos; incompatibilidad del frente multiempresa no publicado. Mitigaciones son las fronteras, evidencia, constraints, gates y pruebas anteriores. No hay reparación histórica ni selección productiva implícita.
