# Actualización v2

El estado vigente de resolver, guards AMEX y revocación está en
[expanded-read-authority-v2-20261016.md](expanded-read-authority-v2-20261016.md).
Las cifras y pendientes del texto original siguiente describen la entrega v1.

# Candidato aislado: lecturas ampliadas, diagnóstico y contrato de desarrollo

2026-10-16. Repositorio `fmfg03/samchatint`. Base remota verificada:
`a983be79e1ad8a6b5aa56affe27a945160ac8166` (main, PR489).
Rama local `feat/connector-reads-diagnostics-20261016`. Repositorio Git copiado
sin hardlinks y worktree independientes del trabajo PDF. No se incorporan
commits ni cambios no guardados de `fix/cfdi-pdf-amounts-20261015`.

Ver [seguimiento de owners reales e integración](expanded-read-integration-20261016.md).

**Estado: candidato local, no conectado.** No se cambian el catálogo ni la
composición aceptada de INFORME, sus cinco herramientas, la delegación, el rol
`samchat_ro_fmfg`, el servicio ni el bridge. No hay grants, credenciales,
publicación GitHub, commit, push, PR, merge, despliegue ni cambios de negocio.

Runtime futuro: conector Agentius Directo hacia owners de SamChat web
`copa_telmex_dashboard.py`. Dominio: documentos, presupuestos, CxC, Payment Run
y AMEX. Datos de esta ampliación: owners PostgreSQL; no puente Supabase.
Los tests usan fixtures sintéticos y bases desechables, no datos operativos.

## Qué está implementado y qué significa

| Dataset | Implementación real del candidato | Límite de evidencia |
| --- | --- | --- |
| `solicitud` | `connector_read_service.read_solicitudes`: SELECT parametrizado, tipo SOLICITUD, UUIDs exactos, torneo y período; sin joins documentales | Consulta real probada en SQLite; política remota pendiente. `monto_total` persistido no acredita pago ni contabilidad |
| `budget.snapshot` | Invoca build_budget_snapshot sin schema, con torneo estricto, executive_read y versión/fechas exactas | Owner y SQL reales probados en PostgreSQL/WASM sintético: presupuesto, IVA/actual, compromiso, período, scope y quality gaps. Driver async/schema completo pendientes |
| `ar.summary` | Verifica versión approved/frozen y ejecuta build_ar_read_model; detecta multiplicidad de matches para retener totales | Owner/helpers/SQL reales en PostgreSQL/WASM sintético: candidatos, vínculos, matches, reversos, límites y moneda. No reconstruye saldo histórico |
| `payment_run` | Reutiliza `list_payment_run_items`; agrega opciones compatibles `ensure_schema`, `document_ids`, `tournament_id`, y marca de importe ausente sólo en selección acotada | Consulta canónica real probada en PostgreSQL/WASM. Defaults heredados conservados. Se prohíbe DDL en esta llamada; corte no acredita pago |
| `amex.statement` | Proyección mínima, cerrada por defecto sin amex_authorized_reader canónico de servidor | Getter/modelo reales probados con SQLAlchemy SQLite; autorización sintética explícita. Falta integrar y probar guards legacy/direct, contexto y tarjeta |

No se ofrece acceso por existir un enum o un nombre de módulo. Ninguno de estos
datasets está expuesto por el executor actual. La ausencia de adapter o de
scope produce denegación, nunca una respuesta financiera vacía equivalente a cero.

`finance_read_adapter.py` sí conecta budget.snapshot, budget.vs_actual,
ar.summary, ar.prematching, cashflow.summary y finance.platform. **No se envuelve**:
algunas llamadas omiten `ensure_schema=False`; su etiqueta read_only no demuestra
pureza. `budget.vs_actual`, prematching, cashflow y finance.platform completos
quedan fuera del catálogo nuevo hasta probar fuentes, alcance y efectos transitivos.

## Identidad y autorización

`ExpandedReads(resolve=..., adapters=..., audit=..., source_commit=...)` recibe
resolución confiable del servidor; no se monta en ningún transporte. La entrada
de consulta no tiene actor, rol, grants, SQL, función ni credenciales.
`Authority`/`DomainScope` son **resultados internos** del resolver, no una nueva
base de permisos ni una facultad que pueda autodeclararse desde el modelo.
Faltan el resolver productivo y su evidencia canónica; no se incluye uno permisivo.

La futura `samchat.capabilities({})` informa catálogo, límites, binding local y
si la autoridad resuelta tiene scope. `candidate_readable` significa únicamente
composición candidata; `production_activation=false` permanece explícito.
No muestra IDs de otros objetos ni permite cambiar identidad.

Antes de abrir la sesión se exige coincidencia de dataset, UUIDs, torneo y
versión. Después de leer, y nuevamente después del recibo de auditoría, se
recarga identidad y se compara actor, instalación, generación/revocación,
caducidad, versión de política y scopes. Cursor ligado por HMAC a esa identidad,
consulta completa, commit y digest de la proyección; cambia cualquier elemento
y se rechaza. Caduca en cinco minutos y una nueva instancia invalida anteriores.
No se infiere visibilidad de SOLICITUD, CFDI, tarjeta o adjunto desde un INFORME.

| Owner de autoridad que debe enlazarse | Regla verificada en esta base | Evidencia pendiente antes de habilitar |
| --- | --- | --- |
| SOLICITUD: `user_routes.py:ver_documento` y guards auxiliares | Propio empleado o rol admitido por esa ruta; excepciones de observador operacional explícitas. La ruta GET también escribe/notifica: no invocarla | Extraer/compartir guard puro, recargar empleado y denegaciones vigentes, demostrar documento/empresa/torneo. El nuevo SELECT no sustituye ese guard |
| Presupuestos: `admin_budget_routes.py`, `_require_budget_access` en `admin_routes.py` | Gate de lectura presupuestal; edición/versión/torneo autorizados | Scope fiscal y versión actual probados; no sustituir con rol admin genérico ni helper legacy |
| CxC: rutas Finance y `ar.service` | Autoridad Finance + objeto/versión/torneo; cobro por match aceptado o vínculo con póliza/fecha canónicos | Resolver empresa/objeto y las denegaciones. Financiero global no se deriva de acceso a documento |
| Payment Run: `can_access_payment_run` | Acceso, gestión y confirmación son facultades separadas | Invocar acceso con empleado fresco y verificar selección de solicitudes/corte/empresa. No conceder manage ni confirm |
| AMEX: guards legacy y amex_direct_common.load_statement | Legacy exige empleado Finance activo; direct añade contexto/tarjeta/actor y schema canónicos | Callback sin binding por defecto; ver matriz detallada en expanded-read-integration-20261016.md |

`docs/private-plugin/README.md` e `identity-authority-map.md` fueron leídos.
El mapping instalación no equivale a RFC/razón social; los vacíos de mapping
se mantienen cerrados. No se usa el bridge Supabase que crea/reactiva/promueve
empleados, cookies simuladas ni `document_confirmation.py`.

## Consultas y significado de fechas

Contrato implementado por `Query.parse` y documentado por `dataset_catalog()`:

```json
{
  "dataset": "solicitud",
  "object_ids": ["UUID exacto autorizado"],
  "tournament_id": "UUID exacto autorizado",
  "date_from": "2026-01-01",
  "date_to": "2026-02-01",
  "fields": ["object_id", "state", "currency", "amount"],
  "group_by": "state",
  "measure": "amount",
  "page_size": 25
}
```

El ejemplo es descriptivo: se exigen UUIDs canónicos reales, no esos placeholders.
`group_by`/`measure` son opcionales; agrupación admite conteo y suma de los campos
registrados, nunca expresiones. No hay joins libres ni SQL ni funciones arbitrarias.
Las sumas separan moneda, retienen resultado si falta importe/moneda admitida y
declaran conteos de vacíos. El conjunto de monedas sumables es MXN/USD/EUR/GBP/CAD;
otra moneda no se convierte ni agrega. Importes deben ser finitos y de magnitud
inferior a 10^15; conteos enteros no negativos. Campos desconocidos no se exportan.

- Máximo 100 objetos, 100 filas por página, 1.000 filas por respuesta de owner,
  366 días, 64 KiB de respuesta, 3 segundos por lectura (configuración máxima 5).
- Intervalos de consulta `[date_from, date_to)`. Payment Run y presupuesto tienen
  owner inclusivo: se envía `date_to - 1 día`. Los servicios usan límites SQL,
  parámetros de valores y guards de fuente, no sustituciones de texto del modelo.
- SOLICITUD filtra fecha de creación. Payment Run filtra fecha programada; una
  fecha efectiva se expone como dato separado, no como criterio tácito de selección.
- Presupuesto: `budget_total` es toda la versión/edición; no se prorratea por el
  período consultado. Actual usa fecha de gasto; compromisos/pagado documental
  usan cohorte documental canónica. No son caja bancaria. Gaps canónicos retienen
  los totales de esta proyección.
- CxC: snapshot de fuentes actuales y antigüedad a la fecha indicada. El año se
  aplica a candidatos como lo define el owner; los cobros/vínculos no se filtran
  retroactivamente. No interpretar el resultado como saldo histórico al corte.
- AMEX: solapamiento con período declarado del estado, no período contable.

Las respuestas incluyen fuente exacta, commit revisado, hora observada, digest
de proyección, período/base, versión de política, cobertura y vacíos explícitos.
`source_version` es hash de proyección observada, **no** un número MVCC, un
snapshot contable inmutable ni prueba de versión de todo el objeto subyacente.
No hay lectura productiva, por lo que no se declara deployed_commit.

## Sesiones y ausencia de escrituras

`postgres_read_session` admite sólo una sesión fresca sin objetos dirty/new/deleted,
desactiva autoflush, establece REPEATABLE READ + READ ONLY y timeouts, verifica usuario
actual/session_user y ausencia de privilegios elevados, y hace rollback al salir.
No acepta `samchat_ro_fmfg` como rol candidato; tampoco crea rol o credencial.
La comprobación completa de grants/funciones/default ACL/herencia y fingerprint
queda en el preflight de integración, pendiente. No se ha conectado esta función
a una base externa. Cancelación y timeout impiden entregar resultados; rollback
local es probado con doble de sesión. Cancelación del driver real requiere Linux
y la integración pendiente, no se acredita mediante un mock.

## Lectura de código y diagnóstico

`TechnicalReads` reutiliza `Sources` del gateway aceptado: root confiable,
manifiesto de rutas exactas, SHA-256 y commit, rechazo de symlinks/junctions,
traversal, ocultos, binarios y secretos detectados. Scope técnico separado por
source_id + commit, sin inferencia desde permisos financieros.

Operaciones candidatas: `source_info`, `source_read` y `diagnose`.
Lectura máxima 100 líneas por llamada, archivo admitido máximo 64 KiB.
Checks admitidos: `source-manifest` (identidad del conjunto revisado) y
`adapter-bindings` (estado candidato). No shell, comandos, procesos, env,
configuración privada, logs libres, paths descubiertos ni checks ejecutables.
Redacción adicional de patrones de credenciales y cuentas, con digest separado
del texto entregado; no se presenta como detector universal de secretos.
Auditoría técnica registra actor digest, política, fuente, operación, path digest,
check y decisión, nunca código, cuentas ni excepción del backend.

## Contrato de desarrollo; integración de executor pendiente

`git ls-files '*agentius*' '*executor*'` sólo localiza el adapter del gateway,
un patch de catálogo y fixtures. En este checkout no está el executor. El seguimiento
localizó y verificó un bundle histórico completo fuera del checkout; ver
[procedencia, contratos wire y límites](expanded-read-integration-20261016.md).
No se implementa un sustituto ni se presenta un state machine de tests como job real.

Catálogo Directo consultado el 2026-10-16: `samchat-dev`, base registrada
`13bb9d05dce94eb9c8209acd672438b0cf4d83f6`, check `activation-stdlib`, manifest
`1056a7cb553f3caf37d4bdddf1d1ba9015e5f1e954f50594b1a39d254edbd31e`.
Su base difiere de este candidato: rebase/revisión explícitos antes de integración.
Operaciones anunciadas work_create/start/resume/message/cancel/status/list/artifact
no acreditan publicador, GitHub push, PR ni desplegador. No se ejecutaron jobs.

`development_contracts.py` valida **solamente contratos locales**:

| Operación futura | Entrada candidata | Obligación del executor real pendiente |
| --- | --- | --- |
| work_create | project, objective, files exactos, checks admitidos | Identidad confiable; fijar repo/base/rama/manifiesto; crear worktree aislado; recibo durable/idempotencia. Texto objetivo no concede shell ni amplía archivos |
| work_start | work_id, expected_revision | Owner/generación vigentes; compare-and-swap durable; sólo prepared; lease/worker aislado |
| work_resume | work_id, expected_revision | Sólo paused; revalidar manifiesto/base/diff/permisos y consumo de recursos; cancelado no se reanuda |
| work_status | work_id, expected_revision | Estado y logs minimizados del owner; no filtrar trabajos ajenos |
| work_artifact | work_id, expected_revision, sha256 | Hash registrado, bytes verificados y redacción antes de descarga; límite y MIME permitidos; sin paths libres |
| work_cancel | work_id, expected_revision | Marcar cancelado durablemente y detener worker; ningún resultado tardío puede publicar o avanzar |

Pruebas/revisión usan IDs de checks registrados y outputs con hash. El único
check observado es activation-stdlib; este runner nuevo necesita registro/revisión
antes de ejecución remota. Preparación de publicación produce sólo metadata de
base/diff y evidencia; booleans de tests/review son estados del contrato, jamás
aprobación remota. No ejecuta git ni red.

Para una fase futura: escritor aislado sólo workspace/archivos aprobados;
publicador separado sólo `fmfg03/samchatint` y rama aprobada, sin merge/admin;
desplegador separado sólo servicio/release aprobados. Nunca shell root genérico.
Commit, push y PR requieren su autorización posterior sobre diff concreto,
evidencia de tests y revisión independiente, base/HEAD y branch exactos.

Confirmación futura de negocio: recibo autenticado server-side con actor,
instalación, acción, objeto, versión autoritativa, payload canónico y hash,
caducidad, nonce de un solo uso, idempotency key y revalidación transaccional.
Un texto no vacío, `confirmed=True` o un boolean del modelo no autorizan.
`document_confirmation.py` no se importa ni se modifica. Pagos, transferencias,
aprobación remota, usuarios, grants y despliegue continúan fuera de alcance.

## Integración/activación exacta pendiente y rollback propuesto

1. Conciliar el bundle histórico verificado del executor con el checkout/runtime
   vigente y traducir el contrato interno al wire real de work_*. Revisar diferencia de base y registrar checks admitidos.
   Mantener las cinco tools INFORME y sus anotaciones/circuito de auditoría.
2. Implementar resolver confiable enlazado a autenticación vigente, owners y
   denegaciones por dominio. Demostrar empresa/tarjeta/objeto/versión donde falte.
   No requiere redefinir roles del producto; sí evidencia antes de abrir acceso.
3. Proponer rol SELECT separado con allowlist de vistas/columnas, sin CREATE/TEMP,
   herencia elevada, DML, secuencias o funciones con efectos. Revisar privileges
   efectivos y hashes de schema/fuentes. No reaprovechar ni ascender el rol INFORME.
   Si faltan vistas/schema, owner debe aprobar migración por separado; nunca startup DDL.
4. Binding de sesión/driver cancelable y auditoría durable minimizada: TTL,
   rotación, protección de directorio y recibo, denegaciones y revocación durante
   consulta/entrega. Revalidación final no elimina una carrera distribuida por sí sola:
   el owner debe demostrar linearización del release de respuesta frente a revocación.
5. Ejecutar owners completos y regresiones de INFORME en Linux/PostgreSQL nativo,
   usuarios distintos, tipos/versiones, denegación explícita, empresa/tarjeta,
   límites, cancelación, esquema ausente, ausencia de escrituras y auditoría.
6. Revisión humana de diff/manifest/permisos concretos y aceptación positiva,
   negativa y de auditoría por dataset. Autorización separada de publicación y
   activación; este candidato no la contiene.

Rollback propuesto, no ejecutado: quitar sólo bindings del catálogo nuevo,
revocar la delegación nueva, cancelar trabajos de esa ampliación y volver al
manifiesto anterior. Conservar la delegación/rol/servicio INFORME. Si posteriormente
se crea un rol exclusivo, retirar sólo sus grants mediante owner autorizado,
con inventario antes/después. No borrar datos financieros ni auditoría; esta
entrega no exige migración ni backfill. Volver al commit anterior revierte el
opt-in de lectura Payment Run sin alterar estados de negocio.

**Canon unchanged:** el candidato conserva invariantes de autoridad, separación
lectura/escritura, COI, Payment Run, CxC y AMEX; no modifica canon ni atribuye
despliegue o aceptación. Hashes LF verificados: producto `7062756077f455741f03027b335f0be6627476c23f9f0b4ab8744f0a9608c99e`,
ingeniería `bd011d2d33deaea655e4bc3f863e61610454520544d477e1cba1560f6fabd47c`,
sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`.
