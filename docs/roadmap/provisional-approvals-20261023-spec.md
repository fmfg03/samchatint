# Flujo provisional de aprobaciones — especificación para revisión

Fecha: 2026-10-23. Estado: **AUTORIZADA HASTA DESPLIEGUE** mediante «cuentas con mi aprobación hasta el despliegue». Historia aprobada: `provisional-approvals-20261023-story.md`. La autorización cubre construcción, comprobaciones, publicación, configuración y aplicaciones auditadas dentro del alcance; no elimina las validaciones ni permite ampliar reglas de negocio.

## 1. Base, frontera y diagnóstico

Runtime `samchat-gastos.service`, entrada `copa_telmex_dashboard.py`; dominio Gastos/Aprobaciones/Control Presupuestal; persistencia PostgreSQL directa y propietarios canónicos existentes. Base de investigación y `main`: `d591cc4a13987d33d2336bcf872401f3a6c7acb6`. Los tres cánones se leyeron en orden y sus SHA-256 coinciden con el registro de esa base. No se modificó producción.

Hallazgos confirmados:

1. `prepare_document_authorization_route()` y `document_route_approver_sql()` dan prioridad absoluta a `referencia_operaciones`, sobreescribiendo rutas de proyecto a Operaciones.
2. La excepción personal actual revisa beneficiario, o solicitante cuando falta aquel; no comprueba ambos con OR. Sus destinos actuales no son GN exclusivo.
3. Los snapshots no siempre se invalidan al cambiar proyecto/personas: `send` conserva rutas antiguas si hay folio.
4. GN y GV comparten actualmente `direccion_general`. Usar esa posición no distingue GN exclusivo; modificar sus asignaciones dañaría otras superficies.
5. La bandeja y las acciones de documentos con folio restringen a GV por ruta. Su rol `admin` no representa hoy la facultad global aprobada.
6. El rechazo de Control ya registra `Aprobacion.comentario`, pero el helper escribe un texto fijo y no recibe motivo. No se necesita nueva columna.

Caso productivo confirmado: `S-26000450`, UUID `7de8b57d-85e3-4e52-ab60-990ab5fc04b6`, RRSS, estado `enviado`, referencia Operaciones **363**, snapshot `director_operaciones` elegible Odilón. El solicitante es de Finanzas y no tiene beneficiario empleado explícito. La secuencia histórica que produjo el folio/snapshot no está demostrada; no se atribuye a un creador de Operaciones.

## 2. Política y configuración acotada

Reutilizar `authorization_positions`, `authorization_position_assignments`, reglas por proyecto y snapshots con `eligible_empleado_ids`. No crear otro almacén de autorizaciones ni conceder roles generales nuevos.

Crear/configurar cuatro claves **exclusivas del owner de aprobaciones**:

| Clave | Miembros activos |
|---|---|
| `aprobador_federico_gn` | Federico GN |
| `supervision_aprobaciones_federico_gv` | Federico GV |
| `aprobadores_finanzas_provisional` | Luis Ángel y Juan Pablo |
| `aprobaciones_escalan_gn` | Odilón, Olof, Luis Ángel y Federico GV |

No modificar las asignaciones organizacionales de Dirección, Finanzas o GOAT; las nuevas claves no otorgan acceso ejecutivo, facultad de pago, contabilidad ni administración. Juan Pablo no pertenece a la excepción personal por compartir la alternativa de Finanzas. Antes de activar, validar miembros exactos, activos y únicos contra el directorio y obtener preview/hash de los cambios de configuración.

Identidades verificadas para ese preview:

| Persona | UUID |
|---|---|
| Federico GN | `89375388-40fb-4412-aade-56a4a807ab7c` |
| Federico GV | `d21a5407-aa5f-42d5-a90e-f3293b4eb907` |
| Odilón | `f9aebcf3-ef28-423c-8a77-f41c9e4e885a` |
| Olof | `7de0ca84-dac9-4456-ae96-0ed26e15bcbb` |
| Luis Ángel | `723ae3e1-4329-4e48-8662-f9d9d0753cca` |
| Juan Pablo | `e3d13040-2360-420f-98a1-516440ef63c3` |

Resolver solicitante y beneficiario efectivo por sus identidades canónicas, incluyendo la Cuenta de Gastos donde corresponda. La excepción personal se evalúa en ambos y prevalece sobre la matriz. No inferir empleado a partir de nombres/RFC de proveedor ni del capturista cuando exista solicitante explícito.

| Proyecto | Aprobadores asignados, salvo excepción personal |
|---|---|
| Liga Telmex Telcel de Béisbol | Odilón |
| Copa Telmex Telcel de Fútbol | Odilón |
| De la Calle a la Cancha | Odilón |
| Futbolito Bimbo | Odilón |
| La Merced | Odilón |
| Becarios Telmex (México Siglo XXI) | Odilón |
| Gastos Administrativos - Operaciones | Odilón |
| Otros Proyectos | Odilón o Federico GN |
| Homeless World Cup México | Odilón o Federico GN |
| Gastos Administrativos - Dirección General | Federico GN |
| Gestión de Patrocinios | Federico GN |
| Gestión RRSS y Transmisiones | Federico GN |
| Promoción de Negocios | Federico GN |
| Gastos Administrativos - Administración y Finanzas | Luis Ángel o Juan Pablo |
| Otros ingresos | Luis Ángel o Juan Pablo |

La pertenencia de Odilón se conserva mediante la configuración acotada del aprobador de Operaciones, comprobando el UUID indicado. Resolver proyectos por identidad registrada; sus nombres son etiquetas de la tabla y no sustituyen el UUID.

## 3. Propietario de ruta, snapshots y folios

Extender `project_authorization_service.py`, sin duplicar reglas en cada canal:

- Separar ruta asignada, supervisión de GV y política de emisión de folio. Un número ya emitido deja de ser una fuente de autoridad para sobreescribir la matriz/personas.
- Compartir el mismo resultado entre `resolve_project_authorization_route`, preparación/snapshot, predicado SQL, `actor_is_route_approver` y destinatarios de notificación.
- Etiquetar los snapshots nuevos con versión/fuente de política y huella de contexto de proyecto, solicitante y beneficiario efectivo. Persistir esa huella SHA-256 junto con versión y motivo en el campo existente `source VARCHAR(100)`, con un formato acotado/versionado como `p20261023:project:<sha256>` (menor de 100 caracteres). El parser del owner acepta fuentes legacy y distingue el motivo de su representación persistida; no se agregan columnas ni se introducen claves ficticias en posiciones/IDs elegibles. Comprobar contexto y elegibles actuales antes de reutilizar el snapshot. Registrar el contexto completo y rutas anterior/nueva en un evento técnico de `Aprobacion` con acción distinta de aprobar/rechazar, actor real y comentario estructurado; ese evento nunca acredita aprobación financiera. No actualizar snapshots de documentos finales como efecto de una lectura. Probar que cambios de proyecto/personas/asignaciones y fuentes desconocidas no reutilizan autoridad obsoleta.
- Antes de invocar el allocator, resolver el proyecto efectivo. RRSS y Patrocinios producen **cero llamadas al allocator**, con o sin creador de Operaciones. Aplicar esa regla a los creadores de solicitudes personales/terceros y a la creación/envío de informes y cuentas.
- No asignar primero un número para después borrarlo. Mantener el lock/secuencia canónica para los casos que sí lo requieran.
- La matriz nueva define aprobadores, no una nueva política general de folios. Para otros proyectos y excepciones personales se conserva el comportamiento previo del emisor, separado de las nuevas posiciones; las altas de reglas de aprobación no deben alterar incidentalmente ese comportamiento. Verificar equivalencia en pruebas. No inferir nuevas excepciones de numeración para Otros Proyectos/Otros ingresos.
- Folios históricos, incluido 363, no se borran, renumeran, reciclan ni descuentan del contador. En RRSS/Patrocinios se conservan como evidencia histórica sin asignar autoridad a Odilón.

## 4. Autoridad global de Federico GV y consistencia entre canales

Comprobar la asignación activa y explícita de `supervision_aprobaciones_federico_gv`, compartida por bandeja, detalle, acciones y guardas de Telegram. No usar un bypass para cualquier admin/superadmin. La ruta asignada a GN se conserva aunque GV pueda intervenir como supervisor.

En `transition_documento_workflow`, ampliar únicamente la elegibilidad del actor de aprobación/rechazo para GV. Conservar locks y controles de estado, aprobación previa, reembolsos derivados, comprobaciones parciales, gastos activos, importes y evidencia AMEX. No añadir una prohibición de autoaprobación que el owner actual no tiene ni retirar guardas financieras. La supervisión no autoriza pago, corte contable o aprobación automática.

Web, SQL de bandeja, botones por fila/detalle, Telegram/live runtime y notificaciones deben coincidir. El asistente sigue llamando al owner canónico. Los accesos generales existentes de otros perfiles no se amplían.

## 5. Motivo opcional en Control Presupuestal

Extender el formulario `GET /documentos/control-presupuestal` y la acción `POST /documentos/control-presupuestal/asignar-lote`, conservando sus rutas y contratos de autorización. Agregar un campo opcional compartido por rechazo individual y de lote. Normalizar espacios; vacío/ausente permite rechazar.

Pasarlo a `_reject_control_presupuestal_document` por ambas ramas y persistirlo en `Aprobacion.comentario`, conservando el contexto de Control cuando esté vacío. Mostrar el motivo en el historial mediante los renderizadores escapados existentes. La selección de varias partidas de un informe produce un único rechazo de su documento padre.

Conservar: INFORME → borrador, limpieza de envío y reapertura de CuentaDeGastos; SOLICITUD → rechazado. No exigir partida presupuestal para poder rechazar. El rechazo operativo normal conserva su requisito de motivo; la nueva opcionalidad es sólo de Control. No requiere migración de esquema.

## 6. Pendientes existentes y corrección del caso confirmado

Antes de la activación, generar inventario **read-only** de pendientes afectados con ruta/contexto actual y propuesto, conteos y hashes. Separar visibilidad/calculado de persistencia: no corregir registros silenciosamente al desplegar ni al consultar una bandeja.

Preparar una operación canónica acotada para S-26000450 y, sólo si se aprueba su manifiesto, otros pendientes enumerados. Preview completo, actor autorizado, hash confirmado, transacción única y comparación de estado/contexto bajo lock. Ruta propuesta GN; conservar UUID, estado `enviado`, importes, referencias y evidencia. Registrar ruta anterior/nueva y razón en auditoría. Bloquear si avanzó, cambió el contexto o se aprobó/pagó. La operación no emite notificaciones externas sin autorización específica.

No ejecutar `repair_tournament_budget_reconciliation.py`: su alcance actual exige INFORME operativo y presupuesto editable y no cubre esta solicitud. Reutilizar sus patrones de preview/hash/receipts, no sus selectores ni sus mutaciones presupuestales.

Compatibilidad de auditoría comprobada contra el esquema servidor: usar la acción no financiera existente `editar` con el discriminador JSON `action=actualizar_ruta_autorizacion`, conservando actor, contexto y rutas anterior/nueva. El preview valida los CHECK de `aprobaciones` obtenidos de PostgreSQL y vincula sus definiciones al hash del plan; una restricción ausente, desconocida o incompatible bloquea antes de escribir. Se prueban las representaciones TEXT y VARCHAR sin modificar el esquema productivo.

## 7. Archivos previstos y ejecución por fases

- Política/configuración/folios: `services/project_authorization_service.py`, `services/documento_service.py`, configuración versionada de las tablas existentes y herramienta acotada de preview/aplicación de pendientes.
- Decisiones: `services/documento_workflow_service.py`.
- Compatibilidad de consumidores: copia pura de la política en `samchat/readonly_gateway`, con equivalencia AST y fixtures sintéticas actualizadas; la proyección limitada conserva autoridad no resuelta sin ampliar vistas, permisos ni ámbito. La instalación independiente del gateway permanece fuera del despliegue.
- Web/Control: `routes/user_routes.py`, con cambios delimitados a formularios, emisión de folio, predicados, controles y motivo. Se preservan los cambios ajenos del checkout primario.
- Telegram: `services/documento_telegram.py`, y `services/telegram_document_runtime.py` únicamente si su interfaz lo requiere; adapters del asistente sólo si una prueba demuestra que el owner compartido no basta.
- Pruebas próximas existentes: `test_project_authorization_service`, `test_operations_reference_policy`, `test_operations_route_entrypoints`, `test_pending_approval_visibility_controls`, `test_document_detail_approval_controls`, `test_telegram_document_approvals`, `test_budget_control_gate`, `test_reimbursement_document_semantics`.
- Documentación de reglas/canon y recibos de configuración/aplicación. Regenerar y verificar `docs/private-plugin/route-inventory.json` y `route-matrix.csv`: el campo opcional cambia el contrato del formulario POST.

No nuevas dependencias, background jobs ni cambios de esquema previstos. Las altas/actualizaciones de configuración son una aplicación de datos explícita, respaldada y auditable, separada del despliegue de código. Ningún ensure de startup ejecuta DDL o rellena identidades.

## 8. Aceptación y comprobaciones

Parametrizar cada proyecto para SOLICITUD/INFORME; excepción por solicitante y por beneficiario, con la otra persona distinta; GN exclusivo y Juan Pablo fuera de las cuatro personas protegidas. Probar cada alternativa y dos aprobadores concurrentes: un solo efecto válido, sin segunda transición ni reversión.

Probar GV con/sin folio, fuera de su ruta asignada, y negativos para otro admin, asignación supervisora inactiva y configuración incompleta. La inactivación de supervisión niega la nueva facultad de decidir fuera de ruta, sin retirar los accesos de lectura que ya tenga por su rol. Conservar negativos de estado final, reembolso, AMEX y comprobación parcial. Comprobar compatibilidad de las posiciones nuevas con `authorization_profile_service` sin conferirles elegibilidad ejecutiva.

Probar RRSS/Patrocinios con creador Operaciones, cambios de proyecto/personas, snapshot antiguo y folio histórico. Assert cero invocaciones al allocator en nuevas emisiones excluidas y preservación de 363 en el caso real. Probar proyecto desde documento/CuentaDeGastos y coherencia entre todos los canales.

Control: motivo omitido, vacío, espacios, texto y HTML; uno/lote y varias partidas del mismo padre; motivo persistido/escapado, una sola decisión, estados y permisos conservados. El rechazo operativo normal sigue exigiendo motivo.

Ejecutar suites acotadas, formato/lint de archivos cambiados, cobertura de instrucciones modificadas ≥85%, pruebas nativas PostgreSQL de rutas/configuración/concurrencia/rollback y los checks exigidos por CI. Clasificar fallos ajenos; no declarar completa una validación fallida/no ejecutada. Después, validación independiente de sólo lectura y revisión humana del diff antes de PR. No se han ejecutado estas pruebas de implementación durante la investigación.

## 9. Despliegue, datos, recuperación y validación de negocio

El usuario autorizó continuar hasta despliegue el 2026-10-23. Commit, push, PR, merge, configuración, corrección seleccionada y despliegue se ejecutarán únicamente después de sus comprobaciones y con recibos propios, sin solicitar de nuevo la misma autorización. No incluye nuevas reglas de negocio ni reparación de documentos finales.

Antes de efectos: respaldo/hash de posiciones/asignaciones/reglas/snapshots afectados, validación del directorio/proyectos y del caso. Mantener códigos/configuración compatibles durante el cambio; no activar una política que el release servidor no entienda. Revertir código sólo con su configuración compatible; no reabrir documentos finales ni reasignar folios como rollback.

Verificar release exacto, selector único, health/readiness y guardas. UAT autenticada separada: GN, GV, Odilón y uno de Finanzas comprueban bandejas/decisiones; Control comprueba motivo opcional. No enviar mensajes ni notificaciones a esos usuarios desde esta tarea sin autorización.

## 10. Evaluación de canon

**Canon unchanged.** La política provisional configura la autoridad por personas/proyectos y la supervisión explícita dentro del owner de aprobaciones y de las tablas existentes. El canon ya exige autoridad configurada, identidad activa, separación entre lectura y escritura, transiciones canónicas y auditoría; estos requisitos se mantienen. No cambia la definición de producto, el owner de persistencia, las reglas de pago/contabilidad/reembolso/AMEX ni la elegibilidad organizacional de Dirección. La matriz detallada se versiona en esta historia/especificación, sin convertir una configuración provisional en una nueva regla general del canon.

Se descartó durante la revisión documental una propuesta de ampliación redundante del canon: no se editó ni aprobó automáticamente ningún archivo protegido. El Sweep conserva su inventario histórico. La PR declarará este motivo concreto y verificará que los tres hashes continúan coincidiendo con el registro.
