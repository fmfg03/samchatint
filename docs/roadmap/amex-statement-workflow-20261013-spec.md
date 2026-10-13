# Especificación técnica: recorrido AMEX y separación de pendientes

Fecha: 2026-10-13. Base revisada: `4b971ab3ff4576216a7009aaf883d1351b7c7eb2`. Runtime: `copa_telmex_dashboard.py`, `samchat-gastos.service`; dominio dueño gastos/AMEX, PostgreSQL directo. Los tres canons se leyeron y sus hashes coinciden con el registro.

Fase: historia aprobada por usuario con «aprobada». Especificación, arquitectura de persistencia y textos exactos de canon aprobados explícitamente por el usuario con «aprobada» el 2026-10-13. Implementación autorizada; publicación Git, migración y despliegue productivos requieren sus checkpoints posteriores.

## 1. Historia y aceptación vinculantes

Historia aprobada: `docs/roadmap/amex-statement-workflow-20261013-story.md`. Retirar informes/partidas de Odilón de la cola manual AMEX, conservar íntegros los cargos del banco, y ofrecer tres pasos: cargar estado → resolver pendientes → confirmar/descargar pólizas. Una póliza inicial completa por INFORME; nunca una póliza ficticia por carga. Confirmación Finance y aceptación en COI son hechos diferentes.

Identidad verificada en lectura productiva: Odilón UUID `f9aebcf3-ef28-423c-8a77-f41c9e4e885a`. Nueve informes de la lista tienen beneficiario explícito consistente en documento/cuenta (87 partidasAMEX). `I-138907` tiene beneficiaria Bibiana y conserva atención normal. Alicia es titular/capturista de las nueve cuentas; no usarla para atribuir gastos a Odilón.

## 2. Hallazgos que condicionan el diseño

Hoy el CSV crea ExpenseReport independientes `origen='amex_batch'`, tarjeta last4 y banderaAMEX; no conserva archivo, identidad de carga ni período del estado. Redirige a la misma pantalla de carga. La conciliación agrupa por mes de fecha de gasto, que puede diferir del período del estado. No se presentará esa agrupación como evidencia de un estado bancario real.

Solo `confirm_amex_approved_informe_cut` y su corte canónico contabilizan el INFORME completo. `recognize_expenses` devuelve prepared; la excepción statement-only no genera póliza. Un cargo huérfano mantendrá un bloqueo visible, sin inventar nuevo modelo contable ni informe aprobado.

## 3. Persistencia explícita propuesta

Añadir owner-run migration nueva, sin modificar las migraciones anteriores:

1. `amex_statement_imports`: UUID; FK tarjeta; FK titular declarado; período declarado (inicio/fin válidos); SHA256 del archivo exacto; nombre sanitizado; actor Finance; created_at; parser_version; conteos y resumen de filas válidas/omitidas. Identidad de replay única para archivo, tarjeta, titular, período y parser. Columnas de identidad/resultado inmutables después de completar la transacción.
2. `amex_statement_import_items`: UUID; import FK RESTRICT; número original de fila; imported_expense_id FK RESTRICT nullable; estado imported/skipped y código de motivo; mínimos datos normalizados para auditoría, sin almacenar credenciales/tarjetas completas. Unique(import,fila), unique(imported_expense_id), checks que impidan status imported sin Expense. No se enlazan filas omitted a una Expense inferida.
3. `amex_statement_cut_links`: import FK RESTRICT; cut FK RESTRICT; actor y fecha de declaración; hash y payload versionado de solicitud/fuentes seleccionadas. Unique(import,cut). INSERT-only; misma clave y mismo payload devuelve recibo existente, distinta declaración produce conflicto explícito. El FK fija el corte real; JSON no reemplaza ownership financiero.

Los tres modelos se excluyen de startup create_all mediante un conjunto owner-run específico unido a OWNER_MIGRATION_TABLES; no se añaden a AMEX_OWNER_MIGRATION_TABLES del verificador aprobado-INFORME anterior, cuyo contrato permanece separado. Nuevo verificador metadata-only comprueba tablas/columnas/tipos/nullability/FKs/uniques/checks y guards inmutables antes de activar. No DDL desde GET, servicios o helpers. Rollback de schema rechaza tablas pobladas. Sin backfill, reconstrucción de archivos históricos ni reasignación de gastos.

El vínculo estado→corte es una declaración de navegación, no un match financiero. La conciliación real conserva AmexReportBankAssociation y sus guards; no se introduce otra identidad económica.

## 4. Importación y compatibilidad

Nueva carga exige tarjeta activa, titular y período explícitos. Usa el parser/matcher fiscal actual y los mismos validadores, sin introducir matching de informe por importe/fecha/persona. El período no altera las fechas originales. Un archivo multimes es admisible conforme al parser actual; el CFDI mensual consolidado conserva su restricción actual de un mes y muestra su motivo.

Importación y metadatos se confirman en una sola transacción. Filas válidas y omitidas quedan documentadas. Si un fallo de base aborta la transacción, no continuar guardando en sesión fallida: rollback de toda la carga. Una carga con filas omitted permanece marcada parcial y exige atención; no declarar estado completamente listo. Si todas son inválidas, no generar un falso estado cargado ni gastos.

Reintento exacto devuelve la carga original sin nuevas Expense. Unicidad/bloqueo serializa concurrencia. El mismo archivo con otra tarjeta/titular/período o parser incompatible produce conflicto, sin sobrescribir ni duplicar automáticamente. Archivo distinto para tarjeta/período ya cargados muestra advertencia y exige confirmación explícita de que es una carga adicional; no adivinar equivalencia por montos/fechas. Corregir errores no habilita un backfill ni reemplazo silencioso de la primera carga.

Rutas de carga anteriores conservan contrato y permisos. El formulario nuevo usa el contexto obligatorio; consumidores legacy sin período continúan su flujo anterior, identificados como carga legacy sin identidad reconstruida y sin afirmar que pertenecen a un estado nuevo. Refactorizar el trabajo de importación en helper compartido sin ampliar interpretación de importes, signos ni fiscalidad.

## 5. Política única de atención

Nuevo `amex_attention_policy.py`, UUIDs versionados y documentados, siguiendo el patrón explícito de PaymentRun; conjunto inicial contiene solo la identidad confirmada de Odilón. Validar UUIDs y existencia de la identidad, no nombres. No construir exclusión con SQL ILIKE ni tratar la identidad como un permiso financiero.

Resolver Documento/Cuenta con beneficiario explícito: employee/provider; documento y cuenta deben ser compatibles. Ausente, contradictorio, múltiples informes candidatos o combinaciones de beneficiarios ambiguas permanecen visibles con motivo. No fallback al titular/capturista, al aprobador ni a la tarjeta. Se permite fallback a Cuenta únicamente si su beneficiario es explícito e inequívoco y documento no tiene uno contradictorio. Configuración inválida desactiva exclusión y muestra aviso; jamás oculta todo.

La misma clasificación filtra descubrimiento de informes pendientes, partidas sin tarjeta, selectores de partidas de conciliación legacy, recibos report-first sin banco y sus contadores. La lista de informes AMEX predeterminada y el nuevo recorrido no incluyen los de atención por Cuenta. Los cargos importados nunca se filtran, incluso si su titular declarado es Odilón; enlaces de soporte/association existentes conservan acceso y estado real.

No filtrar cobertura fiscal, reconocimiento/cortes, CFDI/DIOT/budgets, períodos, exportaciones congeladas ni los guards de los 14 históricos. Los informes excluidos mantienen su detalle financiero canónico y un acceso explícito desde Cuenta/INFORME con texto «Preparar póliza del informe»; ahí se resuelven sus pendientes de tarjeta/fiscalidad/cuenta y se confirma el corte, sin repetir conciliación por partida en la cola AMEX.

## 6. Pantallas y contratos propuestos

Nuevo router server-rendered bajo `/admin/contabilidad/amex/estados`, montado por user_routes como el router AMEX existente. Misma autoridad activa Finance/admin/superadmin; GET puro, POST explícito. No frontend/framework/dependencia nuevos.

- GET `/estados`: tarjeta/período/cargos/pendientes/avance y un botón Continuar. Avance contable y estado bancario aparecen separados. El período refleja la declaración persistida, no fechas de compra inferidas. Cargas antiguas quedan en acceso secundario «Cargos anteriores», sin inventar archivo o estado.
- GET/POST `/estados/nuevo`: tarjeta, titular, período, CSV, resumen y respuesta de carga; al confirmar válida, redirigir a su UUID en pendientes. Rutas canónicas legacy siguen disponibles como accesos de compatibilidad, no como paso obligatorio nuevo.
- GET `/estados/{id}/pendientes`: cargos íntegros y faltantes concretos con acción contextual. Las herramientas existentes de CFDI/identidad/vínculo se conservan mediante formularios o links con contexto de regreso validado. El selector permite declaración explícita de fuentes no excluidas; no sugiere que una selección equivale a asociación bancaria.
- GET `/estados/{id}/polizas`: fuentes declaradas y vista completa de cada INFORME, incluso con partidas fuera de tarjeta/período de la carga. Muestra bloqueos canónicos, decisiones por conjunto y fecha contable explícita. Fuentes excluidas se atienden desde su Cuenta, sin contaminar este listado.
- POST `/estados/{id}/informes/{informe_id}/confirmar`: un INFORME entero por confirmación; llama al propietario canónico con actor/confirmed/fecha/expected_source_key/versiones/approval/decisiones actuales. Corte y link se guardan juntos en la transacción. Fallo de link revierte el corte nuevo y todos sus movimientos.
- POST `/estados/{id}/cortes/{cut_id}/incorporar`: reutilización explícita del corte congelado, validando pertenencia a informe/fuentes declaradas y estado. No vuelve a confirmar otro payload ni crea póliza nueva. Modificaciones actuales que requieran ajustes se muestran como tales; no fingir que un corte antiguo cubre partidas nuevas.
- GET `/estados/{id}/exportar.{xlsx,csv,zip}`: usa exclusivamente cut links reales y snapshots congelados; preserva una póliza completa por INFORME y metadata canónica. Si falla un corte solicitado, no emitir un archivo parcial silencioso. Descarga no registra aceptación por COI.

Los paths anteriores son sufijos del prefijo completo declarado, salvo la ruta inicial. Retornos solo a rutas locales allowlisted; UUIDs/Format/autoridad se validan servidor, XSS escaping y privacidad sin IDs/nombres en errores/logs generales innecesarios.

«Falta informe aprobado» identifica el cargo huérfano, ofrece acceso al flujo existente de Cuentas/Informes o soporte y mantiene bloqueo de nueva póliza independiente. Registrar esa excepción no lo convierte en conciliado/listo.

Una confirmación no mezcla informes en una transacción financiera global: cada resultado es una póliza completa y se muestra por separado. Avance de estado puede ser «Pólizas generadas 2/3», sin presentar errores restantes como resueltos. Banco puede seguir pendiente con las pólizas ya generadas; ese control no bloquea artificialmente el corte válido. Filas invalidas/importación parcial sí conservan advertencia de integridad del estado.

## 7. Implementación acotada

Archivos previstos:
- Nuevos: `services/amex_attention_policy.py`, `services/amex_statement_import_service.py`, `services/amex_statement_workflow_service.py`, `routes/admin_amex_statement_routes.py`, inspector/script de schema y migración/rollback `20261013_amex_statement_workflow.sql`.
- Existentes: `models.py`, frontera owner-run `amex_schema_policy.py`, `user_routes.py` solo import/router/colas/contexto/enlace de informes; `admin_amex_accounting_routes.py` solo descubrimiento/contexto. Preservar servicios financieros/report-first/association ya aprobados salvo el uso de sus interfaces.
- Docs: historia/espec/reporte/rollout; inventario/matriz de rutas; dos canons y su registro únicamente con textos expresamente aprobados abajo.
- Tests: unidades policy/import/read model/routes; PG import/replay/concurrency/invariancia; browser tres pasos y responsive/XSS. No dependencias nuevas, daemon, automatización de matching ni PaymentRun adicional.

## 8. Verificación de aceptación

1. Beneficiario Odilón con capturista Alicia excluido en todas las colas/contadores/selectores; capturista Odilón y beneficiario Bibiana incluido. Ausente/conflictivo/provider conserva atención, sin pérdida por joins. Nueve informes objetivo fuera; I-138907 normal.
2. Cargos bancarios, sus totales y estado siempre conservados; política no cambia persistencia financiera, fiscalidad, exports ni guards históricos.
3. Carga con período/fechas distintos, warnings y filas skipped; replay concurrente sin duplicados, conflicto de contexto y cambio de archivo con warning, falla DDL/readiness sin runtime create_all, transacción fallida sin estado fantasma.
4. Cargo huérfano muestra causa y ruta de resolución, sin aprobación fabricada ni póliza nueva.
5. INFORME completo multitarjeta/multiperíodo visible; no cortes parciales; fuente/versiones/cuentas activas caducadas bloquean. Reutilización explícita/corte congelado y replay exitoso conservan actor/fecha/id.
6. Fallo en link/póliza revierte unidad entera; export falla cerrado si cualquiera de los cortes elegidos invalida; ninguna descarga marca COI importado. FK/triggers de evidencia y rollback poblado protegen historial.
7. Autorización activo/roles, GET sin escrituras, context returns, XSS con archivos/nombres/conceptos; Playwright desktop/mobile carga→pendientes→pólizas/download.

Ejecutar suites AMEX existentes, nuevas PG con DB aislada, tests offline de inventario, guards de registro/regresiones/packaging y CI requerida. Cobertura de líneas modificadas ≥85%. Lint/tipos acotados + comparación con deuda heredada, sin ocultar fallos. Verificación de aceptación y revisión independiente de solo lectura antes de diff/PR.

## 9. Release y límites de autorización

Historia aprobada no autoriza código antes de esta espec. Aprobar espec autoriza implementación y diseño de DDL, no aplicación productiva. Commit/push/PR/merge y migración/deploy se autorizarán sobre resultados verificados de esta entrega; las autorizaciones ejecutadas para PR480 no se reutilizan para publicar esta nueva entrega. No reparaciones de los 14 históricos ni backfill/import/reclasificación masiva.

Migración aditiva, backup validado, inspección metadata-only, release exacto, selector único, guards/health/readiness/ACL y rollback preservados. Rollback de código puede restaurar navegación anterior conservando metadatos nuevos y finanzas; no permitir borrar importaciones/vínculos mediante down-DDL. UAT Finance separada.

## 10. Textos exactos de canon propuestos para aprobar

Producto, nueva enmienda en sección9.1; fecha2026-10-13, razón recorrido AMEX/separación de atención; evidencia historia/espec de esta entrega:

«AMEX dispone de un recorrido por importación de estado de cuenta con identidad persistida de archivo, tarjeta y período declarados. Su avance separa pendientes operativos, asociación bancaria y disponibilidad contable. La importación no aprueba informes ni origina pólizas. Finanzas selecciona fuentes explícitas y confirma cada INFORME completo mediante el corte canónico; vínculo de navegación no acredita conciliación. La política de atención por beneficiario explícito puede retirar informes y partidas de la cola manual sin ocultar cargos bancarios, alterar su estado ni excluirlos de controles financieros. Identidad ausente o contradictoria permanece visible. Cargas históricas no reciben identidad de estado reconstruida.»

Ingeniería, nueva enmienda en sección8.2.1; misma fecha/razón/evidencia:

«El recorrido AMEX conserva imports e items con archivo exacto, tarjeta, titular, período y actor declarados; replay de importación no crea cargos adicionales. Tablas y guards se provisionan mediante migración owner-run, excluida de startup. Una política de presentación por UUID de beneficiario explícito se comparte entre descubrimiento, selectores y contadores, sin alterar owners financieros ni cubrir conflictos de identidad con fallbacks al capturista. Vínculos de navegación usan FK a cortes reales y guardan confirmación/payload; no sustituyen AmexReportBankAssociation. Confirmación y link por INFORME son transaccionales, con decisiones y source/version actuales; exportación usa snapshots completos sin cambios de granularidad. Cargos sin fuente aprobada permanecen bloqueados para generación independiente. Datos financieros/históricos y límites de permisos se preservan.»

Actualizar hashes del registro en el mismo PR tras aprobación expresa de estos textos. Sweep Canon unchanged: inventario histórico no se reescribe ni esta entrega acredita aceptación productiva.
