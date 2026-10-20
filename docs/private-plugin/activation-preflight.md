# Preflight de activación privada — pendiente de aprobación

Fecha: 2026-10-01, Ciudad de México. Instalación: SamChat actual de Plataforma Sports; sólo cuentas existentes y sus facultades actuales. Este documento propone configuración, no registra ningún cambio real. Publicación del código, conexión, credenciales persistentes, migraciones y despliegue siguen pendientes.

Canon unchanged: esta unidad añade código no conectado y pruebas aisladas sobre el contrato vigente. No modifica login, roles, tres razones sociales, contabilidad, aprobaciones o runtime. Las modificaciones canónicas futuras se presentarán a revisión humana antes de ejecutarse.

## Rutas y configuración propuestas

La base propuesta usa `sam.chat` porque el runtime canónico está en `copa_telmex_dashboard.py`, con login `/login` y cookie firmada `samchat_session`. No se ejecutó una consulta al dominio ni al servidor.

| Elemento | Propuesta concreta | Cambio real / evidencia que falta |
|---|---|---|
| Issuer | `https://sam.chat/plugin-oauth` | Montaje y revisión del servidor de autorización sobre login existente. Ningún issuer publicado hoy por este código. |
| Recurso/audience | `https://sam.chat/mcp` | Streamable HTTP protegido por OAuth, challenge correcto y validación cada petición. Hoy sólo SDK en memoria. |
| Resource metadata | `https://sam.chat/.well-known/oauth-protected-resource/mcp` | Responder resource exacto e issuer anterior; no anunciar servidores alternos. |
| Authorization metadata | `https://sam.chat/.well-known/oauth-authorization-server/plugin-oauth` | Sólo code + S256 + cliente aprobado; no declarar refresh, DCR/CIMD o private_key_jwt no implementados. |
| Autorización / consentimiento | `/plugin-oauth/authorize`, `/plugin-oauth/consent` | Reutilizar `get_current_empleado`; negar suplantación, cuenta ausente/inactiva, CSRF/browser/state/draft incorrectos. GET prepara; POST acepta explícitamente. |
| Token | `/plugin-oauth/token` | Sólo POST authorization_code; código hash, PKCE, cliente/callback/resource exactos, consumo único y audit en una transacción. Sin refresh por ahora. |
| Desconexión en SamChat | `/plugin-oauth/connections/{grant_id}/revoke` | POST con cuenta propia/CSRF. Ruta propuesta; servicio local implementado. Logout web y desconexión del plugin siguen acciones distintas. No afirma un endpoint RFC7009. |
| Claves públicas | `/plugin-oauth/jwks` | Fuente y rotación de claves públicas revisadas. Endpoint pendiente; los tests inyectan claves públicas. |
| Cliente/callback | Cliente preautorizado y callback **copiados de la configuración real de la conexión** | No adivinar redirect URI ni registrar cliente/grant. La unidad admite exactamente un cliente/callback; CIMD URL no implementado en el store y DCR no habilitado. |
| Scopes iniciales | `direction:read`, limitado a tools verificadas; `get_profile` requiere autenticación | Es techo del consentimiento, no permiso de negocio. Todas las guardas actuales siguen siendo necesarias. Nuevos scopes por familia sólo tras prueba de autoridad. |
| Instalación/organización | Namespace fijo de esta instalación, proporcionado por host confiable | No proviene del modelo/token/selector. No concede acceso a los tres RFC; falta mapeo canónico por operación donde figura gap. |
| Vigencia | Prototipo: consentimiento 300s, código 120s, token 300s; sin refresh | Revisar tiempos operativos, reloj, límites de solicitudes y reconsentimiento. No convertir fixtures en política de producción. |

La [documentación oficial de autenticación](https://developers.openai.com/plugins/build/auth) exige metadata, authorization-code con PKCE S256, resource/audience y verificación por petición. El plugin local no demuestra distribución, disponibilidad universal por plan de paga o conexión a ChatGPT. La skill `plugin-creator` no estuvo disponible y no se inventó scaffold/validador oficial.

## Consentimiento sin cambiar silenciosamente el login

El código recibe un resolver de navegador confiable; éste debe usar la sesión canónica firmada y recargar el empleado. No implementa otro password checker ni usa el puente Supabase que crea/reactiva/promueve usuarios.

La prueba HTTP usa el SessionMiddleware real y `get_current_empleado` cargado del repositorio, con empleados sintéticos. Su helper `/__fixture_session` **sólo vive en tests** y no autentica contraseñas. Los campos `fixture_binding`/`fixture_csrf` de esa cookie también son sólo fixtures. No se modificó el contenido de ninguna cookie real ni se debe montar ese helper en producción.

Antes de conectar, resolver la implementación del browser binding y CSRF. Recomendación: estado transitorio específico de consentimiento, separado de identidad/roles, con cookie HttpOnly/Secure/SameSite adecuada y claves/configuración aprobadas; vinculado a la cuenta canónica actual y al borrador. No copiar campos del fixture a la sesión real sin revisión. No interpretar una cookie de suplantación como consentimiento de cuenta propia.

## Almacenamiento y permisos requeridos

- Connection/session factory suministrada por el host para la base Postgres canónica. No pedir DSN, password, token o `.env` al usuario. La implementación local usa SQLAlchemy Core/Engine; antes de montarla en el runtime async debe adaptarse a su SessionMaker/AsyncSession o aislar la unidad síncrona sin bloquear el event loop. Conexión real, cancelación, pool y versión Postgres objetivo siguen sin verificar.
- Revisar y, sólo después de aprobación, aplicar `20261001_private_oauth_storage_review.sql`: cuatro tablas `private_oauth_pending`, `private_oauth_links`, `private_oauth_codes`, `private_oauth_grants`, FK al ID existente de `empleados`. La migración no crea empleados ni cambia roles.
- Verificar existencia/esquema de `agent_action_receipts` de la migración canónica `20260919`. El adapter usa ese contrato para audit en la misma transacción OAuth. Que la migración exista no demuestra que esté aplicada en producción.
- El rol de runtime requeriría SELECT de la proyección ID/activo de empleados; INSERT/SELECT/UPDATE acotados en las cuatro tablas OAuth; INSERT/SELECT en recibos para escritura y readback. **Ese SELECT no basta para la implementación actual:** `current_employee()` usa `SELECT … FOR UPDATE`, que [PostgreSQL exige acompañar de UPDATE sobre al menos una columna](https://www.postgresql.org/docs/16/sql-select.html). La prueba con rol NOLOGIN descartable confirma el rechazo `42501`. Resolver y verificar una frontera de bloqueo segura y de privilegio mínimo antes de activar; no se autoriza conceder UPDATE sobre empleados ni quitar el bloqueo sin revisar carreras de desactivación. No DDL en startup, privilegios sobre tablas fiscales/tournament ajenas al servicio, grants universales ni permiso de aprovisionamiento. Un propietario separado aplica migraciones aprobadas.
- Aprobar persistencia y rotación de clave de firma OAuth y protección del estado de consentimiento. Tests crean claves y secretos sintéticos en memoria; no existen credenciales/grants productivos de esta tarea.
- Definir retención/purga de drafts/códigos vencidos y grants/receipts, respaldos, rollback y monitoreo mínimo sin bearer/code/verifier/state/CSRF/datos personales en logs. No usar el helper Customer Success que oculta fallos/ejecuta DDL como prueba de audit obligatorio.

Se guardan hashes de código, state, CSRF y browser binding; jti/grant/link/perfil/actor interno y scopes permiten revocación y trazabilidad. No se guardan bearer tokens, claves privadas, contraseñas, correo, teléfono, OCR, prompts, archivos o datos de menores.

## Gates técnicos antes de habilitar

1. Composición real revisada, feature gate inicialmente cerrado y prueba de no suplantación con sesión autenticada existente. Claves, cliente/callback y tablas reales confirmados por responsables autorizados, sin crear acceso automáticamente.
2. Transacciones y row locks en Postgres objetivo, sin AUTOCOMMIT; orden empleado→vínculo→grant; consumo condicional único; expiración revalidada tras espera y antes del commit. Audit y consumo/grant se confirman juntos, falla de auditoría revierte ambos. Reintentar transacción completa sólo si no se devolvió resultado, nunca duplicar emisión/commit.
3. Revalidación por petición de grant/vínculo/empleado, y guardas actuales antes/después de lecturas. Isolación por cartera/torneo/razón social demostrada con el servicio propietario: `organization_for_scope` sigue sin implementación productiva comprobada. Un namespace fijo no resuelve ese gap.
4. Transport MCP real y auth challenge, metadata/callback, rechazo de métodos/params duplicados y límites de tamaño. Respuestas sensibles no-store; ninguna ruta de tests montada. Sin dispatcher libre, SQL/SSH o código suministrado por modelo.
5. Verificación de conexión privada con cuentas de paga autorizadas, recepción de perfil opaco y recibos; no prometer disponibilidad universal por plan. Publicación pública no autorizada.

## Aprobación agrupada que corresponde al siguiente paso real

Cuando exista la composición concreta revisada, el padre coordinará una sola aprobación para: **publicar el código revisado**, **aplicar/verificar schema en la base canónica**, **provisionar la clave OAuth y autorizar cliente/callback**, y **montar/conectar el plugin privado en SamChat actual con el alcance read demostrado**. Cada elemento debe mostrar diff/configuración y rollback. Este preflight no ejecuta ninguno ni solicita autorización genérica para operaciones de negocio nuevas.

Las 44 acciones y revisión deportiva permanecen en el inventario completo y deshabilitadas hasta demostrar su servicio/autoridad/confirmación/idempotencia/auditoría. Aprobar documento no significa transferir dinero. No se usó el fallback deportivo que inventa nacimiento/teléfono/categoría. Frontend activo y paridad UI siguen sin fuente versionada demostrada en esta tarea.
