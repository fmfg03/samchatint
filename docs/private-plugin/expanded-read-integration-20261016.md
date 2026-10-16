# Actualización v2

Resolver canónico, enlace AMEX legacy/directo y revalidación separada ya están
implementados en el candidato. Ver estado, evidencia y fronteras pendientes en
[expanded-read-authority-v2-20261016.md](expanded-read-authority-v2-20261016.md).
El texto siguiente conserva el análisis y evidencia histórica de v1.

# Seguimiento: owners reales e integración pendiente

2026-10-16. Misma base `a983be79e1ad8a6b5aa56affe27a945160ac8166` y rama
`feat/connector-reads-diagnostics-20261016`; rama PDF intacta. Este seguimiento
sustituye la descripción inicial de evidencia basada sólo en dobles. No hay
commit, instalación nueva, grants, publicación, job remoto ni activación.

## Evidencia real y correcciones

`test_expanded_real_owners.py` ejecuta los cuerpos originales de
`build_budget_snapshot`, `build_ar_read_model` y sus helpers/SQL. La carga AST
omite imports del bootstrap de modelos, el exportador openpyxl y el import de
tipos AsyncSession, sin reemplazar funciones de negocio ni resultados SQL.
El transporte convierte parámetros SQLAlchemy a PostgreSQL/WASM desechable y
restaura timestamps serializados por JSON. No equivale al driver async nativo.
Las tablas sintéticas usan columnas canónicas y CREATE TABLE extraídos; algunos
constraints/FKs se relajan sólo en el fixture. No acredita el schema productivo.

- Presupuesto: versión aprobada, torneo exacto, presupuesto 1000, compromiso
  116 y gasto 58 con IVA 8 producen actual 50. Otro torneo/versión draft se
  rechazan. Febrero conserva presupuesto anual pero excluye la ejecución de
  enero. Moneda desconocida retiene totales. El owner de schema no se llama.
- CxC: plan mensual 2000 y CFDI 116; vínculo aprobado y match aceptado 50
  producen balance 66. Reversar el match retira la cobranza. USD o el límite
  de 500 candidatos retienen totales parciales. Se prueban otro torneo y versión.
- AMEX: getter canónico `get_amex_statement_import` y clase original
  `AmexStatementImport` ejecutados con `Session.get` SQLAlchemy real sobre SQLite.
  UUID/JSONB se compilan a CHAR/JSON sólo para ese fixture. Se comprueba la consulta
  por ID y que filename/summary/tarjeta no salgan en la proyección. La fachada
  awaitable y la autorización son sintéticas: **no prueba el guard de AMEX directo**.
- El transporte de pruebas rechaza todo SQL distinto de SELECT; READ ONLY de
  PostgreSQL rechaza DDL. Preparación de fixtures modifica sólo la base efímera.

Se corrigió el candidato: CxC ahora consulta `_select_budget_version` con
`ensure_schema=False` y exige ID, edición y estado approved/frozen, antes del
owner AR que antes sólo devolvía la versión pedida. AMEX ahora falla con
`AMEX_AUTHORITY_UNBOUND` sin un lector autorizado enlazado desde el servidor;
`capabilities` no anuncia ese adapter como legible por defecto. No se proporciona
un callback permisivo de producción. Los dobles anteriores se conservan como
pruebas de contrato y están identificados como tales.

## Resolver exacto: fuentes que deben componerse

Cadena requerida: binding del servidor → identidad vigente → empleado mínimo →
perfiles y denegaciones → guard del dominio → objeto/scope → consulta →
revalidación. `Authority` sigue siendo el resultado interno de esa cadena;
no existe todavía un resolver productivo ni un nuevo almacenamiento de grants.

| Dominio | Ruta/owner canónico | Condición pendiente de integración |
| --- | --- | --- |
| SOLICITUD | `user_routes.ver_documento`, `_can_access_read_only_informe_document` y excepción comprobante | Compartir predicado puro de propietario/roles/excepciones. No invocar el GET, que escribe fecha/notifica. No heredar permisos desde INFORME ni adjuntos |
| Presupuesto | `admin_routes._budget_can_view`, `_require_budget_access` y `admin_budget_routes` | Superadmin, departamento Dirección o correo canónico autorizado; `budgets.read` por sí solo no reproduce la ruta. Agregar denegación `presupuestos.ingresos/ver`, alcance fiscal y versión/torneo |
| CxC | `admin_routes` ruta AR y `routes.dependencies.require_admin_finanzas` | Rol financiero admitido o permisos efectivos `admin.finanzas.manage`, `finanzas.manage`, `admin.*`; denegación `admin.finanzas/ver`; demostrar empresa/versión/torneo |
| Payment Run | `payment_run_service.can_access_payment_run` | Empleado fresco y denegación financiera; seleccionar documentos admitidos. No usar manage/confirm como sinónimo de acceso |
| AMEX legacy | `amex_statement_workflow_service._authorize`, `_statement` | Empleado activo, FINANCE_AMEX_ROLES, modo legacy y denegación de contabilidad. Getter por UUID no autoriza |
| AMEX directo | `amex_direct_common.load_statement`, `authorize`, `configuration_for`, `actor_scope` | Reutilizar `load_statement(session, actor_id, import_id, 'ver', locked=False, finance=True)` o extracción equivalente probada. Actor desde binding confiable; nunca del modelo |

AMEX directo **ya tiene** mapping canónico de actor/tarjeta/empresa/receptor:
`AMEX_DIRECT_AUTHORIZED_CONTEXTS`. No se leyó el valor real ni se modificó.
`load_statement` autoriza antes de consultar fuente, limita tarjetas, comprueba
schema instalado sin DDL, resuelve contexto inequívoco y vuelve a autorizar.
Su regla de config_hash contempla cortes existentes para lecturas: no sustituir
esa regla por una interpretación nueva. La composición futura del callback debe
respetar ese owner y la rama legacy; todavía faltan sus pruebas de integración.
El UUID de torneo del catálogo no prueba alcance fiscal de una tarjeta.

La segunda revisión independiente detectó un defecto heredado del owner CxC:
`matches_by_item` conserva un solo cobro aunque el owner de aceptación permite
parcialidades. Factura 116 y matches 50+20 podían devolver 50/66, no 70/46.
El candidato consulta de nuevo el owner de matches, restringido a versión e
ítems exactos de la proyección, sin DDL, y retiene los totales cuando hay varios
matches aceptados por ítem (`canonical_ar_multiple_matches_totals_withheld`).
Se prueban dos parcialidades, reverso de una, y matches ajenos a versión/ítem.
No se modifica la contabilidad ni se simula una suma reparada. El arreglo completo
del owner AR queda pendiente de una entrega específica.

La sesión candidata pide y verifica REPEATABLE READ + READ ONLY para coherencia
entre consultas de fuente y diagnóstico de multiplicidad. El resolver de
revocación debe consultar autoridad fresca **fuera de esa snapshot**; no reutilizar
la sesión financiera para acreditar permisos vigentes. La cobranza canónica
también admite vínculo con `collection_poliza_id` y `collection_date`; el gap
de salida se corrigió para mencionar ambas vías de evidencia.

`DelegationFile` y `BoundPrincipalResolver` existentes prueban integridad del
binding (hash, UID/GID, modo, caducidad). `load_report_delegation` pertenece a
INFORME y no debe ampliarse silenciosamente. No usar `_load_empleado_proxy_by_id`
(lee password_hash y PII) ni convertir `get_current_empleado` en autoridad remota:
hay rutas de error que permiten acceso. La lectura mínima nueva debe seleccionar
sólo ID, activo, rol, departamento/correo necesarios y permisos efectivos. Los
errores de perfiles deben denegar; `_load_effective_permissions` absorbe errores.
`explicit_tool_decision` es la frontera de denegación explícita, no el helper
general `can_access_tool`. Estos cambios de owner todavía no están implementados.

## Agentius: hallazgo verificable y frontera restante

Se usaron los accesos existentes `server_info`, `list_projects` y la lectura
admitida `executor-demo/context.md`; no se invocó work_create/start ni shell.
El catálogo actual confirma `0.3.0-prepared`, base SamChat
`13bb9d05dce94eb9c8209acd672438b0cf4d83f6`, manifest
`1056a7cb553f3caf37d4bdddf1d1ba9015e5f1e954f50594b1a39d254edbd31e`,
check `activation-stdlib`. No anuncia publicación ni despliegue.

La búsqueda acotada en los directorios locales de tareas encontró:

- Bundle `agentius-0.2.1-persistent-8097689-bundle1.tar.gz`:
  SHA256 `7d3325977d496ee0e1fa7247ca256070bada52dd7f27d9d8fd903ca15cadc7b8`.
- Manifiesto exacto:
  `4f3f56000ff7552ca88d090738c6ae1cff782eb296e0a6a6978ac33ab38d9070`.
  Se verificaron hoy los **39/39 archivos** de `historical-source/agentius`.
- `worker/contracts.py`:
  `6581e903e6a2cea560424694066f7a3814e4513dca5904667377e45841458406`.
  Su código real pasó ocho verificaciones de sintaxis de create/start/resume/
  status/artifact y denegaciones de campos internos, traversal y método shell.
  No se ejecutó el worker ni se acreditó autorización/state machine.
- Commit parcial documentado `8097689b1fc02999d0cb56436bb545e44ce44650`:
  no identifica todo el bundle. No se encontró URL de repositorio ni revisión
  Git completa probada del executor. No inventar `fmfg03/agentius` ni confundir
  las revisiones SamChat/ISH con una revisión Agentius.

La copia `integrated-candidate` de otro trabajo está marcada no activada; no se
modificó ni mezcló. También hay un gateway HTTPS local con manifiesto propio,
que no es el worker. Los recibos documentales del 4/oct vinculan el bundle a un
release; son evidencia histórica, no inspección nueva del proceso activo.

Para continuar el ciclo remoto se necesita el checkout autorizado cuyo árbol
corresponda a esos hashes, o exportación admitida del árbol/manifest del runtime
vigente, con URL del repo y commit completo provistos por su owner. Puede usarse
el bundle exacto como base de estudio offline; la falta de Git no impide leerlo.
Lo que no está acreditado es su identidad con el runtime actual ni el permiso
para modificar/publicar sus componentes. Ninguna credencial nueva es necesaria
para documentar esa frontera.

El contrato local `development_contracts.py` es INTERNO. La interfaz observada
y el `worker/contracts.py` real usan:

| Operación wire | Campos |
| --- | --- |
| work_create | project, request_id, mode, base_sha, manifest_hash, objective, scope{read_files,write_files,checks}; implement exige story_ref/spec_ref hashes |
| work_start/resume | work_id, expected_contract_hash, request_id |
| work_status | work_id, cursor opcional |
| work_artifact | work_id, artifact_id, cursor opcional |
| work_cancel | work_id, request_id |

`expected_revision` no puede enviarse como `expected_contract_hash`, ni un SHA
local convertirse en artifact_id. Falta adapter de traducción con recibos
auténticos, idempotencia, estados/leases/cancelación y pruebas contra el worker.
El validador sintáctico no acredita que un check esté registrado ni autorizado.

## Pruebas nativas que faltan para coordinar otro entorno

Se necesita Linux desechable con PostgreSQL nativo (`initdb`, `pg_ctl`, `psql`),
socket Unix exclusivo `/tmp/samchat-ro-native-*`, Python compatible, SQLAlchemy,
greenlet y asyncpg; dependencias de importación del runtime SamChat (incluido
openpyxl) disponibles en el entorno de prueba. Aquí no están greenlet/asyncpg/
aiosqlite/openpyxl en el venv, ni cluster Linux. No se instalaron en este seguimiento.
Node/PGlite y SQLAlchemy ya estaban disponibles y se reutilizaron.

1. Ejecutar `test_live_informes_native.py` (12 pruebas actualmente omitidas) y
   los casos legacy con `native_legacy_engine.py`, mediante runner/harness Linux
   existente, en cluster exclusivamente sintético. Estas fixtures instalan y
   retiran schema/roles DENTRO del cluster desechable: nunca apuntar a producción.
2. Portar `PgTransport` a AsyncSession real y ejecutar los mismos owners con
   schema completo y fixtures canónicos, sin carga AST ni imports sustituidos.
   Verificar READ ONLY efectivo, current_user=session_user, base y search_path,
   timeouts, rollback, cancelación del driver, cierre y no reutilización con
   identity map/autoflush pendientes. La prueba actual de sesión es un doble.
3. Inventariar permisos efectivos y funciones: sin CREATE/TEMP, membresías
   elevadas, DML/secuencias ni funciones con efectos; fingerprint del schema.
   El candidato no concede/revoca estos permisos. El rol INFORME sigue aparte.
4. Probar resolver real con dos empleados y revocación concurrente: perfil,
   denegaciones, versión/objeto/empresa/tarjeta, aislamiento entre legacy/direct,
   contexto ambiguo/cambiado, schema AMEX ausente y no acceso a adjuntos relacionados.
5. Ejecutar contratos work_* con el worker real aislado y sus controles Linux
   (UID/GID, SO_PEERCRED, socket, filesystem, cancelación/recuperación); todavía
   sin commit/push/PR. Sólo después registrar el check adicional para este alcance.

Rollback propuesto no cambia: retirar sólo bindings y delegación nueva, cancelar
sus trabajos y restaurar manifiesto anterior. No hubo activación que revertir.
Los cánones permanecen sin cambios: se conservan reglas de autoridad y negocio.
