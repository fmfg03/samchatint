# Especificación propuesta: tratamiento AMEX, prestaciones sin proyecto y deudor del socio

Fecha: 2026-10-23. Estado: para aprobación humana; sin código implementado, migración aplicada ni configuración cambiada. Historia aprobada: `/tmp/samchat-amex-direct-account-treatment-story-20261023.md`, corregida por decisión expresa del usuario y su «apruebo».

## 1. Base, evidencia y alcance

Runtime: FastAPI `copa_telmex_dashboard:app`, `samchat-gastos.service`; dominio Gastos/AMEX directo; persistencia PostgreSQL directa. Release observada: `d591cc4a13987d33d2336bcf872401f3a6c7acb6`. Los tres canons de producto, ingeniería y sweep fueron leídos y sus hashes revalidados contra `docs/roadmap/samchat-convergence-register.md`; coinciden con los ya revisados. El checkout principal tiene cambios ajenos; construcción posterior en worktree aislado desde `origin/main` actualizado.

Hallazgos confirmados:
- `classify_direct_items` exige exactamente torneo o project_code, BudgetConcept y edición. El envío y la asignación repiten el scope por proyecto.
- `assign_direct_budget_line` usa el owner `assign_budget_movement_to_line(... source_kind='amex_direct', ensure_schema=False)` y obtiene la cuenta del BudgetConcept; la tarjeta determina el pasivo. Se preserva ese owner.
- El corte exige asignación para todos los nuevos `new` / `new_no_cfdi`, tanto en Python como en SQL. Ocultar campos no resuelve el bloqueo.
- BudgetLine/BudgetConcept admiten dimensiones nulas en esquema, pero la lectura productiva acotada no encontró partidas 2026 sin `tournament_id`; sí encontró catálogos administrativos aprobados con mappings. No se presupone que existan partidas utilizables por prestaciones sin configuración explícita.
- El owner AMEX de informes ya reconoce `partner_receivable` con cuenta activa `1170-002-XXX`, motivo y cargo completo al deudor. Reutilizar ese contrato contable sin modificar el flujo INFORME.

Se separa el **destino operativo del gasto** de la **identidad de su asignación presupuestal real**. Una prestación mantiene proyecto/torneo nulos aunque la partida tenga ubicación administrativa en el catálogo vigente. No se crea un torneo ficticio ni se rellena Dirección General automáticamente.

## 2. Matriz de rutas

| Tratamiento explícito | Datos Contabilidad | Presupuesto | Datos de corte |
|---|---|---|---|
| `benefit_expense`: prestación absorbida | Tratamiento y motivo; sin proyecto/categoría/cuenta de gasto elegidos por Contabilidad | Juan Pablo asigna partida/version/concepto autorizados; de ahí sale la cuenta | Asignación real vigente, cuenta mapeada, evidencia fiscal aplicable y pasivo de tarjeta |
| `partner_receivable`: cargo al socio | Cuenta deudora autorizada del socio y motivo | No se envía ni se imputa como gasto presupuestal | Cuenta deudora vigente, cargo completo y pasivo de tarjeta |
| `project_expense`: gasto de proyecto | Contexto, categoría y edición del flujo vigente | Asignación por Juan Pablo | Mantiene íntegramente los requisitos actuales |

No hay tratamiento por defecto por tarjeta, comercio, porcentaje, ausencia de proyecto o ausencia de CFDI. Partidas ya reconocidas o cubiertas como prior/settlement conservan sus owners y no admiten esta reclasificación. Abonos no se convierten en cargos por elegir tratamiento.

## 3. Persistencia y prueba vigente

Añadir mediante migración owner-run:
- `amex_direct_account_treatment_decisions`: decisiones append-only con UUID, item/import/context, revisión, acción set/revoke, tratamiento, cuenta deudora nullable, snapshot de identidad/cuenta, motivo, actor, fecha, fuente efectiva/version/hash, request/idempotency key y hash del payload. Únicos por item/revisión y clave de solicitud; referencia exacta a las filas/autoridad existentes.
- Cabeza nullable `account_treatment_decision_id` en AmexDirectItem y referencia nullable a esa decisión en AmexDirectCutItem; restricciones head→item/context/revisión y snapshot completo.
- Prueba de scope presupuestal elegido en la asignación/evento y snapshot del corte: UUID de línea, concepto, versión, edición, dimensiones reales del catálogo, cuenta y grant configurado. No se copia su torneo/proyecto a `ExpenseReport.proyecto` ni a los campos operativos del item de prestación.

Una fila existente sin decisión mantiene exactamente el contrato legacy de proyecto; no se le fabrica una atestación ni se cambia su hash histórico. Sólo una confirmación nueva crea el tratamiento. Actualizar proyecciones/contrato de fuente efectiva conservando las pruebas de recuperación temporal y de signos del PR #497.

`budget_status` continúa describiendo la etapa presupuestal, no el tratamiento. Prestación puede ser clasificada/enviada sin concepto inicial; la asignación de Juan Pablo fija atómicamente concepto/versión/partida y mapping. Deudor mantiene campos/asignaciones presupuestales nulos y un estado explícito no aplicable, respaldado por la decisión; no se interpreta un NULL como excepción autorizada.

## 4. Autoridad y catálogo

Reutilizar identidad activa, denegaciones explícitas, permisos AMEX, entidad/RFC/tarjeta y `budget_actor_ids`. Contabilidad elige tratamiento; sólo el owner presupuestal y la autoridad configurada asignan partidas. Elegir prestación no amplía el scope a todos los presupuestos.

Proponer extensión explícita de cada contexto AMEX para:
- Allowlist de partidas/pares versión-concepto disponibles para prestaciones y ámbito de los operadores autorizados a consultarlos/asignarlos.
- Allowlist de cuentas deudoras permitidas en ese contexto; cuenta activa validada con el contrato existente `1170-002-XXX`. La identidad elegida es el UUID de la cuenta del socio y sus etiquetas verificadas, no un nombre convertido en UUID de empleado.

Ausencia o ambigüedad de configuración produce `benefit_budget_catalog_unconfigured` / catálogo de deudores vacío con explicación; nunca un permiso global. La configuración productiva y los UUID exactos deberán revisarse antes de activar la funcionalidad; esta especificación no selecciona partidas por semejanza de nombre, no asigna importes ni crea presupuestos. La aplicación puede admitir partidas administrativamente ubicadas en el catálogo vigente sólo por esa autorización explícita; su scope real queda en la asignación presupuestal, separado del proyecto operativo ausente.

## 5. Owners y transacciones

Proponer `amex_direct_account_treatment_service.py` como owner acotado de las decisiones, reusando carga/autoridad, selección de items, eventos, fuente efectiva y locks existentes; no crear un ledger paralelo. Operación de lote requiere items explícitos, versiones/hash, preview de valores y confirmación. Orden de locks: import/state/context → lock global de reconocimiento AMEX → items/anclas ordenados → decisión → presupuesto/cuentas/versión según owners actuales. Sin llamadas SAT/red dentro del lock.

Set/revoke, cambios de ruta y replay se ejecutan en la UOW existente; un error revierte el lote. No permitir cambiar después de freeze, reconocimiento, prior o settlement. Si hay presupuesto enviado/asignado, exigir devolución/liberación por su owner antes de salir de la ruta. No borrar eventos, vínculos fiscales ni asignaciones por SQL directo. Cambios de cuenta/mapping/contexto/decisión invalidan previews anteriores.

Para prestaciones:
1. Selección explícita guarda tratamiento con proyecto/categoría operativos vacíos.
2. Envío a CP valida el tratamiento y su grant, sin llamar al `_scope` de proyecto con datos falsos.
3. Listado de partidas usa allowlist real y estado/edición/dirección vigentes, no `item.budget_concept_id` aún ausente.
4. Juan Pablo selecciona una partida; derivar y revalidar su concepto, versión, edición y mapping bajo locks. El owner presupuestal persiste una asignación única para la ancla efectiva; no habilitar el endpoint genérico para eludir autoridad AMEX.
5. La proyección de actuals usa esa asignación real para presupuesto y no exige que el gasto tenga proyecto. Una prestación sin asignar permanece pendiente; no aparece como cero ejecutado. Cuenta de gasto y pasivo se fijan en la misma transacción.

Para deudor: confirmar cuenta/motivo bajo autoridad y mantener ausencia de asignación. No incluir sus anclas técnicas en gasto/actuals presupuestales. Conservar la identidad económica bancaria y los controles de reconocimiento compartido; el importe no se reconoce dos veces en el corte.

## 6. CFDI, Sin CFDI y contabilización

La clasificación y la vinculación fiscal son pasos independientes. Elegir ruta conserva CFDI/status/evidencia existentes; no confirma una coincidencia pendiente. Mantener política vigente de Plataforma Sports sobre complementos, PUE/PPD, fechas y evidencias; no crear nuevas reglas de deducibilidad.

Prestaciones con CFDI usan el owner fiscal de gasto existente; prestaciones Sin CFDI conservan la declaración explícita y tratamiento gasto completo sin movimientos fiscales, y siguen bloqueadas sin partida/mapping.

Deudor reutiliza el tratamiento económico ya existente en AMEX: debe completo a su cuenta y haber completo al pasivo de la tarjeta; no origina gasto ni IVA como prestación. Si tiene CFDI, conservar el vínculo y sus controles de identidad/uso sin convertir el cargo en gasto. Si no tendrá CFDI, exigir declaración explícita respaldada por decisión/motivo. El tratamiento actual `full_amount_expense_no_tax` no debe reutilizarse como si fuera gasto: extender el owner de declaración con variante tipada `full_amount_partner_receivable_no_tax`, sin modificar las declaraciones existentes ni aplicar una por inferencia. El usuario revisa este detalle en esta especificación; no se ejecuta todavía.

Extender el corte completo para mezclar las tres rutas. Mantener `new`/`new_no_cfdi` para gastos con presupuesto y añadir cobertura tipada de deudor; cada miembro conserva decisión, fuente bancaria efectiva, importe, cuenta, contexto, evidencia fiscal aplicable y líneas exactas. No admitir deudor mediante un `if` que simplemente salte todas las validaciones. Una sola póliza por estado; abonos, prior y settlement mantienen sus pruebas y saldos.

## 7. SQL, hashes y exportación

Actualizar `amex_direct_check_item`, `amex_direct_check_cut` y guardas de consumo/póliza/snapshot/freeze preservando completos los cuerpos vigentes fuera de las nuevas ramas. SQL valida los mismos tres tratamientos, cabeza vigente, cuenta/scope, fuente/importe, asignación y mapping, privacidad fiscal e igualdad multiconjunto de líneas/snapshot. Una prestación sin asignación y un deudor con cuenta inválida deben fallar incluso mediante SQL directo. Retener pruebas de signos, recuperaciones, exclusión cross-source y no duplicidad fiscal.

Los fingerprints nuevos incluyen decisión/revisión, grant autorizado, identidad/actividad de cuentas y prueba de asignación. Una fuente legacy sin decisión conserva su fingerprint. Exportar sólo snapshot congelado; nunca reconstruir tratamientos desde estado mutable. Compartir owner COI y conservar compactación de encabezados, RFC normalizado y HWC del release actual.

Migración propuesta: `database/migrations/20261023_amex_direct_account_treatments.sql` y rollback vacío. Añadir metadata-contract exacto, exclusión startup DDL, checker/CLI y fixtures al final de la cadena actual. Sin backfill o reclasificación. Rollback con locks y negativa cuando exista evidencia nueva; después de usar la funcionalidad se requiere código compatible y corrección hacia adelante, no borrar decisiones/cortes ni reactivar reglas viejas.

## 8. API y UI

Extender el router AMEX directo existente. Rutas propuestas de preview/confirmación de tratamiento bajo el estado: `/estados/{import_id}/directo/tratamiento/preview` y `/estados/{import_id}/directo/tratamiento`; mantienen autorización, sesión y validación de formulario/confirmación conforme al router real; no se da por existente un control CSRF no verificado. No añadir otro módulo ni endpoint de escritura no autenticado. Set/revoke explícitos con nonce/idempotencia/versiones; definir tipos exactos durante construcción según patrones actuales, sin aceptar campos sobrantes.

En la tabla existente: selector de tratamiento, motivo y selección de lote. Prestación muestra «Requiere partida de Control Presupuestal» y oculta proyecto/categoría/cuenta de gasto manual. Deudor pide cuenta del socio y no ofrece enviar a CP. Proyecto conserva sus campos actuales. Mostrar estado y próximo paso por cargo, cantidad/importe de selección, errores visibles sin perder datos, revisión antes de confirmar y resultado del lote. Mantener estilo de la plataforma y accesibilidad/uso móvil.

Cola de Juan Pablo contiene prestaciones y gastos de proyecto, diferenciados. Para prestación permite elegir partida sin pedirle primero proyecto/categoría al capturista; etiqueta completa real del catálogo/versión para evitar duplicados de nombres. Contadores no dejan deudores pendientes de presupuesto y no presentan prestaciones sin partida como listas para póliza.

No añadir background jobs, dependencias, descarga SAT, matching automático, PASE ni cambios en pago de tarjeta.

## 9. Archivos previstos y fases

Backend: nuevo owner de tratamiento; `amex_direct_models.py`/reexport de modelos; common/effective source/context/budget/accounting/no_cfdi y fiscal/posting sólo donde se comparten validaciones; `src/samchat/budgets/service.py` para la fuente AMEX sin proyecto y exclusión de deudores. Contrato/checker/policy de schema, migración/rollback y metadatos de rutas regenerados con herramientas existentes.

Frontend: `src/devnous/gastos/routes/admin_amex_direct_routes.py`; mantener patrón de formularios renderizados y recursos existentes, sin framework/dependencia nueva.

Docs: historia/spec/reportes en `docs/roadmap`, canons y hashes sólo después de aprobar el texto de §11. Cualquier archivo adicional por un owner no previsto se justificará antes de ampliarlo.

Fases: (1) modelo, decisión/autoridad y migración; (2) presupuesto y contabilización completa; (3) UI/lotes/colas; (4) aceptación de estados mixtos y regresión/validación independiente. No publicar la UI como funcional si el corte sigue exigiendo proyecto para prestaciones.

## 10. Verificación, promoción y riesgos

Pruebas unitarias: rutas, cuenta/scope/denegación, versiones, lotes, catálogos, estados y fingerprints legacy. PostgreSQL real: migración al final de cadena, replay/rollback vacío y rechazo poblado, contraescritura de guardas, tres rutas, asignación real sin proyecto operativo, presupuesto sólo una vez, deudor excluido de actuals, fuentes corregidas PR #497, races de presupuesto/matching/tratamiento/corte y snapshot inmutable. Aceptación: estado mixto con CFDI, Sin CFDI, prestaciones pendientes/completas, deudor válido/inválido y proyecto; terminar en una póliza/COI con sumas y cuentas exactas. Browser: lote homogéneo y selección mixta explícita, sin proyecto obligatorio para prestación, partida aún necesaria, deudor sin envío, errores/loading/reintento y layout vigente.

Regresión obligatoria: AMEX legacy/report-first/bridge/settlement/prior, fiscal/IVA vigente y complementos, no-CFDI, señales de Google/Amazon corregidas, HWC, RFC y encabezados COI. Checker schema, compile, estilo/tipos focales, gates release, inventario, CI obligatorio y cobertura de líneas cambiadas ≥85%. Reportar skips/deuda preexistente; no llamarlo UAT real.

Promoción posterior al diff aprobado y autorización de efectos: main actual, backup privado con verificación y preimagen, configuración exacta aprobada de catálogos/cuentas sin ampliar roles, owner migration y release compatible coordinados, health/readiness y route denial, manifest/SHA y verificación de un caso real autorizado. No tocar runtime durante esta fase. Validación independiente debe revisar tanto source como SQL/rollback y evidencia de aceptación antes de presentar el cambio final.

Riesgos: cuentas globales sin company_code (se exige catálogo explícito por contexto), catálogos administrativos modelados bajo torneos (scope presupuestal separado y auditado), fixtures históricas que omiten la capa nueva, estados viejos sin decisión, fiscalidad distinta de gasto/deudor, doble conteo de anclas técnicas, drift de mapping, concurrencia y downgrade después de usar decisiones. No regularizar ninguno silenciosamente.

## 11. Texto exacto de canon propuesto para revisión humana

### Añadir al canon de producto

Fecha: 2026-10-23. Diseño propuesto para aprobación explícita junto con esta especificación; su publicación no acredita despliegue ni aceptación financiera.

«AMEX directo distingue por decisión explícita el gasto de proyecto, la prestación absorbida por la empresa y el cargo a cuenta deudora del socio. Las prestaciones requieren Control Presupuestal: la autoridad configurada asigna una partida real y su mapping determina la cuenta, sin exigir ni fabricar proyecto o categoría operativos para el gasto. El cargo al socio exige cuenta deudora autorizada y motivo, y no consume presupuesto ni origina reembolso o gasto de prestación. La contrapartida permanece en la tarjeta. La clasificación no sustituye la evidencia fiscal ni reescribe vínculos o cortes; estados mixtos mantienen confirmación Finance y una póliza completa, con historia y unicidad preservadas.»

### Añadir al canon de ingeniería

Fecha: 2026-10-23. Diseño propuesto para aprobación explícita junto con esta especificación; migración y promoción requieren recibos propios.

«Las rutas contables de AMEX directo usan decisiones append-only, cabeza vigente y prueba de fuente/cuenta/contexto en el corte. La asignación presupuestal de una prestación conserva el scope real de la partida separado del proyecto operativo nulo y se ejecuta por el owner y autoridad presupuestales existentes; catálogos autorizados son explícitos y su ausencia no concede acceso global. La cuenta deudora comparte el contrato canónico AMEX de cargo completo al socio y no se proyecta como gasto presupuestal. Fuente efectiva, hashes, declaraciones fiscales tipadas, SQL guards y snapshots resuelven el mismo tratamiento y protegen concurrencia, no duplicidad e inmutabilidad. Fuentes legacy sin decisión mantienen su contrato; no hay backfill implícito. Esquema sólo por migración owner-run; evidencia nueva impide downgrade destructivo.»

Sweep unchanged: el cambio no altera el diagnóstico transversal de arquitectura/runtimes del sweep. Actualizar sólo los hashes de canons efectivamente modificados al incorporar los textos aprobados; sin edición automática silenciosa de artefactos protegidos.

## 12. Evaluación de compatibilidad y política instalada por el owner

Decisión de implementación, 2026-10-23, dentro del objetivo aprobado de grants explícitos y preservación de vínculos. El usuario extendió expresamente autorización hasta despliegue sin solicitar otra. No cambia la matriz de tratamientos ni amplía permisos de negocio.

La configuración AMEX vigente se liga por hash completo al contexto fiscal. Agregar sólo los nuevos grants haría fallar la lectura de estados ya cargados; recrear sus contextos liberaría CFDI/presupuesto y contradice la historia. Comparar exclusivamente una proyección que quite los nuevos grants mantiene identidad fiscal, pero por sí sola no ancla en SQL la autoridad de esos grants: una decisión podría fabricar su propia lista.

Se conserva el hash completo al crear contextos nuevos. Para contextos anteriores se admite sólo coincidencia exacta con la configuración completa o con su proyección canónica que elimina exclusivamente `benefit_budget_line_ids`, `benefit_budget_pairs` y `partner_debtor_account_ids` en el contexto y sus actor_scopes. Todos los campos anteriores de identidad, tarjeta, RFC, entidad, actores, permisos y presupuesto permanecen vinculados; no se actualizan contextos ni sus CFDI.

Añadir como prueba de autoridad `amex_direct_account_treatment_policies`, por configuración fiscal autorizada y hash completo (no una por estado). Contiene el documento canónico completo y su proyección/base hash, identidad de ámbito, revisión, actor y evidencia de instalación. Cada decisión nueva refiere por FK a una política instalada y deriva sus grants de esa política; SQL no confía en listas suministradas por la decisión. El owner y el corte exigen que la política coincida con la configuración vigente completa; retirar un grant invalida confirmaciones anteriores. Una revocación segura puede retirar un tratamiento obsoleto conservando autoridad base, sin habilitar una cuenta o partida retirada.

La tabla de políticas rechaza INSERT/UPDATE/DELETE de aplicación. Su configuración autorizada usa una CLI del owner con preview, manifiesto, identidad de operador, confirmación e idempotencia: transacción con bloqueo exclusivo, retiro temporal únicamente de su guarda de DML, inserción exacta de nuevas políticas y reinstalación de la misma guarda antes de commit. No se usa un GUC arbitrario como autorización. El checker falla cerrado si la guarda está ausente o deshabilitada. El límite de amenaza es DML ordinario; un owner con capacidad de cambiar DDL puede también alterar las guardas financieras existentes y no se presenta como excluido criptográficamente.

No se borran políticas antiguas ni se concede acceso global. Esta tabla y su CLI son la ampliación técnica necesaria para cumplir el contrato ya aprobado de configuración explícita, sin rediseñar roles/credenciales ni introducir un ledger. Pruebas adicionales obligatorias: grants falsos, inserción/modificación de política por DML, guarda deshabilitada, cambio de campos anteriores, política retirada, compatibilidad de contexto antiguo con CFDI intacto y uso de la misma política por cargas futuras.

## 13. Compatibilidad física del campo de proyecto

La integración real confirma que `ExpenseReport.proyecto` es NOT NULL. Para representar ausencia operativa se conserva la columna existente y se guarda la cadena vacía, mientras `AmexDirectItem.tournament_id` y `project_code` permanecen NULL. Lecturas/guardas usan ausencia normalizada (`NULLIF(trim(proyecto),'')`) y no un proyecto ficticio. Se evita una alteración global de la tabla central; la semántica aprobada de prestación/deudor sin proyecto no cambia. La fecha bancaria conserva su representación original con hora; las comparaciones usan su fecha ISO parseada y rechazan ausencia/valor inválido, sin aflojar las pruebas de importe o moneda.

## 14. Retiro de permisos y liberación explícita

Las políticas instaladas y decisiones se conservan inmutables al retirar un grant. Nuevas asignaciones, declaraciones, cambios de fuente y cortes requieren política vigente y no aceptan grants retirados. Para evitar bloquear la corrección de un cargo, se permite sólo la limpieza explícita mediante los owners: revocación Sin CFDI, retiro del tratamiento y devolución presupuestal de la asignación original, sin crear nuevos reconocimientos o asignaciones.

La devolución de prestación requiere actor CP actualmente configurado y activo, misma identidad fiscal/base autorizada, política histórica instalada, scope histórico probado de la línea para CP y Contabilidad, y coincidencia exacta de assignment/line/version/concept/movement_key con el gasto efectivo. La cola puede conservar ese pendiente para devolverlo, sin ofrecer una nueva asignación. Se limpian conjuntamente vínculos de partida/concepto/edición y el mapping que pertenece al owner, antes de cualquier flush; la auditoría guarda la asignación anterior. SQL acepta únicamente las transiciones exactas y su evento autoritativo, no escrituras arbitrarias a una fila que tenga un permiso retirado. Un actor de Finanzas fuera del scope configurado no puede revocar por SQL. Se mantienen denegaciones explícitas, freeze y pruebas de fuente/cuenta/economía.

## 15. Coherencia de la revisión financiera nueva

La revisión independiente identificó que las nuevas opciones/preview/confirm podían admitir en Python una delegación genérica de `clasificar`, mientras las guardas de inserción de decisiones exigen rol financiero activo. Los tres entrypoints nuevos exigen `finance=True`, además de permiso y scope, para rechazar temprano y visiblemente el caso en vez de aceptar un preview que fallaría al confirmar. Se conserva la clasificación legacy de proyectos y sus delegaciones; no se amplían roles ni permisos. Los operadores contables configurados para esta entrega mantienen su rol financiero y Juan Pablo su autoridad de asignación presupuestal. Prueba de regresión: actor no financiero con permiso explícito puede conservar la clasificación legacy, pero no obtiene opciones ni preview/confirmación financiera nuevos.
