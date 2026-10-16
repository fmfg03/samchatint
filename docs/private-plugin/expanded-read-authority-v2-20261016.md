# Candidato v2: autoridad canónica, AMEX y revocación

2026-10-16. Base `a983be79e1ad8a6b5aa56affe27a945160ac8166`, rama aislada
`feat/connector-reads-diagnostics-20261016`. Este documento actualiza los pendientes
del paquete v1, que se conserva íntegro. No hay commit, publicación, instalación,
grants, activación ni acceso productivo. Canon unchanged: se reutilizan las reglas
vigentes, sin ampliar facultades de negocio ni el rol INFORME.

## Integración implementada

`canonical_authority.py` contiene `ServerBinding`, `CanonicalAuthorityResolver`
y `compose_candidate`. El proveedor del binding es exclusivamente composición
confiable del servidor: identidad autenticada, empleado, instalación, generación,
caducidad y ceilings de objetos. El modelo no aporta esos campos. Los ceilings
son un límite adicional; no sustituyen los guards canónicos ni conceden permisos.
La composición no registra herramientas ni abre conexiones por sí sola.

El resolver carga únicamente `empleados.id,activo,rol,departamento,correo`;
`_load_effective_permissions(..., fail_closed=True)` aporta perfiles activos.
El parámetro nuevo conserva el comportamiento web previo por defecto. Los tokens
se entregan en `permissions` y `effective_permissions`, como requieren sus owners.
`explicit_tool_decision` mantiene denegaciones persistidas incluso para superadmin.

| Dominio | Función realmente enlazada | Restricción adicional |
| --- | --- | --- |
| SOLICITUD | `user_routes._can_read_documento` extraído de `ver_documento` sin cambiar su predicado | UUID, tipo SOLICITUD y torneo exactos; propietario/roles/excepciones canónicas; no se invoca el GET que escribe/notifica |
| Presupuesto | `admin_routes._budget_can_view` | Denegación presupuestal; versión approved/frozen, edición y línea del torneo; ceilings del servidor |
| CxC | `dependencies.require_admin_finanzas().dependency` | Denegación financiera, versión/edición/torneo exactos; no inferencia por INFORME |
| Payment Run | `payment_run_service.can_access_payment_run` | Denegación financiera, documentos SOLICITUD exactos; no habilita manage/confirm ni DDL |
| AMEX legacy | `amex_statement_workflow_service._authorize` y `_statement(locked=False)` | Actor financiero activo, denegación contable, modo legacy y objeto exacto |
| AMEX directo | `amex_direct_common.load_statement(..., 'ver', finance=True, locked=False)` | Actor/tarjeta/empresa/RFC/contexto según configuración canónica; admisión real de esquema y excepciones de corte canónicas |

AMEX ya tiene un callback concreto en la composición candidata. El adapter suelto
continúa cerrado cuando no recibe ese callback. Se normaliza UUID/texto al entrar.
`MinimalActorSession` evita que los owners AMEX carguen hashes de contraseña al
buscar al empleado; devuelve la misma proyección mínima y delega las otras lecturas.
No hay acceso a adjuntos, cuentas completas, reconocimiento ni locks de escritura.
La procedencia AMEX identifica ahora el lector autorizado compuesto.

El torneo del catálogo AMEX es un namespace del ceiling servidor, **no** una
afirmación de que un estado de cuenta pertenezca a ese torneo: el owner AMEX
delimita tarjeta y contexto fiscal. No se fabrica una relación statement→torneo.
La selección fiscal de presupuesto/CxC y los ceilings de instalación deben ser
aportados por el owner de esa instalación; un enum de módulo no sirve como permiso.

## Revocación fuera del snapshot

`ExpandedReads.query` usa resolución asíncrona antes de leer, después de cerrar
la lectura financiera y después del evento allow de auditoría. Cada resolución
abre y cierra una sesión de autoridad nueva. `postgres_read_session(authority=True)`
exige READ COMMITTED y READ ONLY; la lectura de datos conserva REPEATABLE READ.
No se reutiliza el identity map financiero. El hash de generación incluye empleado,
perfil efectivo y pruebas de objeto/contexto; cambios bloquean la entrega.
La última comprobación precede inmediatamente el retorno: no se promete revocar
bytes ya entregados ni eliminar la carrera inevitable posterior a ese punto.

`capabilities_async({})` aplica el mismo mecanismo y revalida tras auditar. La vía
síncrona rechaza una composición que exige autoridad asíncrona. Los fallos iniciales
de autoridad y cancelaciones se auditan; si no se resolvió identidad, el evento
no inventa un actor. Errores de backend salen como códigos cerrados, sin su texto.

## Evidencia y límites de prueba

`test_expanded_authority.py`: 14 casos nuevos con SQLAlchemy/SQLite reales y datos
sintéticos. Ejecutan cuerpos canónicos de guards y sus dependencias locales,
cargados sin modificar mediante AST de bloques seleccionados; omiten bootstrap
web. Depends/HTTPException son fixtures de framework. Los modelos de AMEX directo
en estas pruebas son proyecciones sintéticas; la prueba anterior del getter conserva
la clase canónica original. No se afirma equivalencia completa de ORM/imports.

Se prueban los caminos positivos legacy/directo completos hasta `gateway.query`,
owner frente a otro usuario, tipo INFORME rechazado, torneo, versiones draft,
perfiles y denegaciones, contexto ambiguo, cambio fiscal, actor no configurado,
modo desconocido, no escalamiento Payment Run, expiración, límites de binding,
fallo seguro/auditable, revocación tras allow, cambio de empleado y cancelación.
SQLite query_only y el transporte SELECT-only impiden escrituras durante lectura.

En una prueba de revocación dos conexiones reales mantienen el snapshot antiguo
con `activo=1` mientras otra confirma `activo=0`; la nueva sesión de autoridad ve
la revocación y bloquea resultados. Es evidencia de separación transaccional en
SQLite; falta repetirla con el driver y aislamiento PostgreSQL nativos.

La admisión de esquema de AMEX directo es un **fixture explícito**, con aceptación
y rechazo. El código candidato llama sin sustituciones al
`require_amex_direct_schema` real a través del owner. Sus constraints, triggers,
hashes y tipos deben aceptarse en un PostgreSQL desechable con schema completo;
esta prueba SQLite no acredita esa frontera ni un esquema productivo.

## Pendientes exactos

| Punto / función o archivo | Dependencia externa exacta | Por qué no se cierra en este checkout |
| --- | --- | --- |
| `canonical_authority.ServerBinding` → transporte | Owner de instalación que vincule sesión autenticada con empleado y ceilings fiscales/objetos, revocables | El resolver está implementado; el checkout no contiene evidencia de binding autenticado de la nueva instalación. No ampliar la delegación INFORME ni crear grants |
| `postgres_read_session`, 12 casos `test_live_informes_native.py` | Linux desechable, socket exclusivo `/tmp/samchat-ro-native-*`, PostgreSQL nativo, asyncpg/greenlet y dependencias de import del runtime | Inventario local sin psql/pg_ctl/initdb ni servicio PostgreSQL; rutas habituales ausentes; WSL no instalado, Docker ausente. No se autorizó instalar ni cambiar servicios. Las doce pruebas exigen explícitamente ese socket |
| `amex_direct_common.load_statement` / `amex_direct_schema_service` | Schema canónico completo en ese cluster desechable; configuración sintética y ORM originales | Guards integrados y probados; admisión real del catálogo no es portable a SQLite. No usar datos/configuración productivos para sustituir la aceptación pendiente |
| Revalidación PostgreSQL concurrente | Dos conexiones nativas y cancelación del driver bajo READ COMMITTED/REPEATABLE READ | Implementación y prueba concurrente local cerradas; falta acreditar semántica específica del driver/rol PostgreSQL |
| Executor Agentius work_* | Árbol autorizado/manifest que identifique el runtime vigente y adaptación wire con recibos auténticos | Se conserva la evidencia offline del paquete anterior. Un contrato sintáctico no demuestra instalación, checks autorizados ni ejecución remota |

## Permisos propuestos y rollback

Nada se concede en este candidato. Para aceptación futura: rol lector separado
del INFORME, sin superuser/CREATEROLE/CREATEDB/replication/BYPASSRLS, sin DML, DDL,
CREATE/TEMP, secuencias o funciones con efectos. Revisar ACLs/membresías/default ACL,
database/search_path y funciones antes de activación; el helper actual no sustituye
ese inventario. SELECT mínimo de empleados, asignaciones/perfiles/reglas, metadatos
de documentos/versiones/líneas y tablas de lectura del dominio. AMEX requiere
también SELECT de identidad/reglas y catálogos en la sesión de datos porque repite
el guard; estados, contextos y existencia de cortes según el owner. No SELECT de
credenciales ni de cuentas bancarias completas. Configuración de tarjeta/contexto
permanece bajo el owner; el catálogo no la expone.

Rollback futuro: retirar sólo el binding/registro nuevo, cancelar lecturas y trabajos
del candidato, volver al manifiesto anterior y revocar únicamente permisos nuevos
que se hubieran aprobado explícitamente. Aquí no hay activación que revertir.
Escritor, publicador de repo/rama y desplegador de servicio/release siguen separados,
sin shell root genérico ni confirmación por texto no vacío.
