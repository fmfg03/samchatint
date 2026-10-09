# SamChat privado: inventario y perímetro revisable

Fecha: 2026-10-01. Base de negocio actual: `6cd122f1030ab39631d77e706a9f5a1a7192231d` (PR445 integrada).
El commit inicial `120a4117925e` permanece en el historial.
Rama: `feat/private-plugin-review-20261001`. Runtime objetivo: `copa_telmex_dashboard.py`;
dominios Gastos/Finance/Operations/Assistant/Registration; datos híbridos Postgres
más puentes Supabase. Evidencia de este cambio: **código local, no conectado**.

**La paridad completa es el requisito final. No está implementada ni demostrada.**
Este entregable no reduce el producto a cuatro tools: registra cada declaración
HTTP localizada y las 44 acciones de `action_router.py`, sin convertirlas en
facultades. Ninguna operación de negocio está habilitada en producción. La
fábrica original sigue inerte; una composición local separada prueba perfil y
lectura Direction con fixtures y guards canónicos. El avance transaccional añade
OAuth local y una prueba HTTP en memoria, sin conexiones a servicios reales.
Ver [autorización sobre la sesión actual y revisión adversarial](session-authorization-review.md),
[OAuth y recibos locales](oauth-local-verification.md),
[flujo OAuth transaccional actual](oauth-transactional-verification.md),
[preflight de activación](activation-preflight.md),
[avance de roundtrips](local-read-verification.md) y
[mapa de identidad/autoridad](identity-authority-map.md).

## Entregables y cómo verificarlos

- `route-matrix.csv`: 561 declaraciones con ruta real, archivo/línea, handler,
  guardas visibles, servicio candidato, tool propuesta, scope/objeto, efecto,
  confirmación, evidencia/idempotencia, pruebas exigidas y estado/gap.
- `route-inventory.json`: mismo inventario con hashes de fuentes, 44 acciones,
  36 AccessTool por defecto y montajes resueltos estáticamente para revisión.
  Se conserva el alias `/copa-america/api/assistant`; una declaración puede
  producir varias rutas. GET no prueba pureza.
- `scripts/private_plugin/inventory.py`: regeneración por AST, sin importar el
  runtime, leer configuración privada o abrir conexiones. Escanea Python en raíz
  y `src/`; registra fallos de parseo (cero en esta base).
- `src/samchat/private_plugin`: contratos de identidad/política/auditoría,
  perímetro fail-closed, catálogo de 44 acciones + 7 etapas de revisión,
  vínculo exacto de confirmación, contrato de reintentos y proyección minimizada
  de validación canónica. Sin dispatcher, ruta HTTP ni feature flag; fábrica MCP
  inerte descrita abajo.
- `adoption_preview.py`: adaptador puro que invoca el promotor CTT canónico
  sobre copias ya autorizadas y devuelve sólo conteos/hash/versión. No valida
  elegibilidad del roster ni guarda datos, y exige diff humano posterior.
- `mcp_boundary.py`: fábrica del SDK MCP real, no montada. Sesiones en memoria
  prueban initialize/list/call: lista vacía y llamadas siempre denegadas;
  contexto confiable e identidad vigente por solicitud. No hay HTTP/OAuth real.
- `tests/unit/private_plugin`: fixtures sintéticos de seguridad, auditoría local,
  integridad del inventario y canons. Invocan el promotor CTT puro; no modelos,
  conexiones de negocio ni servicios externos.

Los nombres de tool de la matriz son propuestas por handler, no schemas finales
ni una recomendación de publicar 561 tools. Alias, vistas HTML y variantes de
exportación pueden agruparse bajo herramientas enfocadas, conservando todos sus
resultados, permisos y casos de prueba. La agrupación no autoriza dispatch libre.

## Matriz funcional por perfil: complemento a las 561 filas

Las facultades se resuelven actualmente desde empleado activo, roles, perfiles,
posiciones, rutas de autorización y guards del dominio; no hay equivalencia
universal rol → acceso. `profile_evidence` del CSV conserva las expresiones
locales de cada handler, **no afirma que sean todas sus guardas transitivas**.
Las filas siguientes explican las familias; el CSV conserva cada operación.

Leyenda: R lectura con pureza pendiente; W escritura/efecto con preview +
confirmación exacta + idempotencia + auditoría + verificación. En TODAS las filas:
estado `DISABLED`, falta adaptador con alcance canónico probado y prueba de
paridad. Pruebas D = deny rol/facultad, empresa, objeto, cartera/torneo y usuario;
C = confirmación/versionado/reintento; F = archivos/faltantes/duplicados;
T = trazabilidad/cifras/estado y lectura sin escritura de negocio.

| Función completa / perfil elegible sujeto a guard canónico | Rutas reales de referencia | Dueño/servicio existente → tools propuestas | Scope y objeto; efecto; evidencia/pruebas |
| --- | --- | --- | --- |
| Notas/comprobantes, gasto personal/terceros, editar/cancelar/adjuntar; solicitante o facultad autorizada | `/gastos/nuevo`, `/gastos/{gasto_id}/editar`, `/gastos/{gasto_id}/adjuntos/{attachment_key}` | `user_routes.py`; `expenses.create_manual_expense`, workflows de receipts → expenses intake/review/create/edit/cancel/assets | expenses read/write, gasto/empleado/empresa/torneo; R/W; fuente+hash+estado; D C F T |
| Solicitud personal, terceros y anticipo; solicitante/beneficiario autorizado | `/documentos/nueva-solicitud-personal`, `/documentos/nueva-solicitud-terceros`, `/gastos-terceros/solicitar-anticipo` | `user_routes.py`; `expenses.create_solicitud_*` → solicitud preview/create/update | documento/beneficiario/empresa/torneo; W; borrador verificado sin enviar/pagar; D C F T |
| Informes/cuentas: crear, asociar gastos, editar, cerrar, saldar, cancelar | `/informes-de-gastos/crear`, `/informes-de-gastos/{cuenta_id}/cerrar`, `/informes-de-gastos/{cuenta_id}/saldar` | `user_routes.py` y servicios de gastos → informe list/detail/draft/attach/close/settle | informe/gastos vinculados y actor; R/W; totales, referencias, fuente; D C F T |
| Enviar/retirar, control presupuestal individual/lote | `/documentos/{documento_id}/enviar`, `/documentos/{documento_id}/retirar`, `/documentos/control-presupuestal/asignar-lote` | `receipts.send_document`, route owners → document submit/withdraw/budget classify | documento/ruta/posición/concepto; W; estado previo/nuevo; D C T |
| Aprobar/rechazar individual/lote; aprobador vigente de la ruta | `/documentos/{documento_id}/aprobar`, `/documentos/{documento_id}/rechazar`, `/documentos/pendientes/accion-lote` | `receipts.approve_document`, `receipts.reject_document`, `user_routes.py` → approval preview/approve/reject/batch | documento/ruta/importe/versión, no rol genérico; W; decisión por documento y parcialidad explícita; D C T |
| Reembolso/devolución/anticipo, historial y evidencias | `/documentos/{documento_id}/registrar-reembolso`, `/documentos/{documento_id}/registrar-anticipo`, `/informes-de-gastos/{cuenta_id}/reembolsos/{reembolso_id}` | `receipts.register_document_reembolso`, owning routes → reimbursement/advance/return operations | informe aprobado/clasificado y beneficiario; R/W; no segundo CP; D C F T |
| Payment Run: programar/cerrar, comprobantes/versiones/revisión/lote, historial/orden | `/admin/finanzas/payment-run`, `/admin/finanzas/payment-run/closures`, `/admin/finanzas/payment-run/pay`, `/admin/finanzas/payment-history` | payment services + admin routes → payment schedule/cutoff/proof/review/history/order | facultades separadas acceso/gestión/confirmación contable; documento/corte/banco; R/W; corte ≠ pago; D C F T |
| Préstamos/abonos/decisiones/programación/constancias | `/prestamos`, `/prestamos/{prestamo_id}/aprobar`, `/prestamos/abonos/{abono_id}/aprobar` | `user_routes.py` + loan services → loan/repayment tools | deudor/préstamo/cuenta/empresa/aprobador; R/W; saldo y comprobante; D C F T |
| Presupuesto: versiones/líneas/conceptos/asignaciones/import/export/transiciones | `/admin/presupuestos`, `/admin/presupuestos/versiones/{version_id}/transition`, `/admin/presupuestos/torneo/{tournament_key}` | `admin_budget_routes.py`, `src/samchat/budgets`, `budgets.*` → focused budget tools | versión/torneo/concepto/mes; R/W; plan/actual/reconciliación; D C F T. No extender legacy |
| CxC, CFDI ingreso, decisiones, cobranza/reversión/contabilidad/export | `/admin/finanzas/cuentas-por-cobrar`, `/admin/finanzas/cuentas-por-cobrar/matches/accept`, `/admin/finanzas/cuentas-por-cobrar/matches/{match_id}/reverse` | `src/samchat/ar`, budget income routes → AR list/link/decide/match/reverse/export | ingreso/CFDI/empresa/torneo/movimiento; R/W; match aceptado ≠ candidato; D C F T |
| Cifras/tableros/cashflow/reportes ejecutivos y exportaciones | `/admin/finanzas`, `/admin/finanzas/cashflow`, `/admin/ejecutivo`, `/admin/finanzas/export.xlsx` | finance_platform/cashflow; `executive.*` → finance report/query/export | facultad/empresa/torneo/período; R (export revisar efectos); fuente/corte/ausente≠cero; D T |
| Contabilidad: cuentas, pólizas/manual/COI, diario/mayor/balanza/estado | `/admin/contabilidad/manual`, `/admin/contabilidad/coi`, `/admin/contabilidad/diario`, `/admin/contabilidad/balanza` | canonical accounting services + `accounting.*` → accounting read/draft/post/edit/export | empresa/periodo/póliza/cuenta; R/W; una póliza atómica por INFORME; D C T |
| Cierres/checklist/reapertura/reclasificación/auditoría | `/admin/contabilidad/cierres`, `/admin/contabilidad/cierres/{fiscal_year}/{fiscal_month}/cerrar`, `/admin/contabilidad/reclasificacion/aplicar` | owning accounting routes/services → close/reopen/reclassify/audit | facultad contable/empresa/mes; R/W; trazabilidad y reversión; D C T |
| Bancos/auxiliares/tesorería/conciliación individual y masiva | `/admin/contabilidad/banco/carga-masiva`, `/admin/contabilidad/conciliacion/bulk`, `/admin/contabilidad/tesoreria-matches/accept` | canonical reconciliation services → bank import/preview/link/unlink/accept/reject/export | movimiento/empresa/cuenta/gasto; R/W; dedup y fuente; D C F T |
| CFDI/SAT: ingesta/jobs/matching/dedup, solicitud y estados | `/admin/gastos/sat`, `/admin/gastos/cfdis/liberar-duplicados/vista-previa`, `/gastos/{gasto_id}/solicitar-cfdi` | canonical CFDI services; `receipts.*` → CFDI intake/status/link/request/dedup preview/apply | CFDI/RFC/empresa/documento; R/W/efecto externo; no credenciales en chat; D C F T |
| AMEX: carga/tarjetas/conciliación/CFDI/pase/pago/notificación | `/gastos/carga-masiva-amex`, `/admin/gastos/amex/conciliacion`, `/admin/gastos/amex/conciliacion/validar-notificar` | owning AMEX routes/services → AMEX intake/reconcile/link/validate | titular/tarjeta/mes/empresa; R/W; minimizar datos bancarios; D C F T |
| Nómina: patrones/cuentas/empleados/incidencias/períodos/prenómina/beneficios/export | `/admin/nomina/patrones`, `/admin/nomina/incidencias`, `/admin/nomina/prenomina`, `/admin/nomina/periodos/cerrar` | payroll services/owning routes → payroll preview/calculate/close/export/configure | facultad nómina/patrón/empleado/período; R/W; acceso restringido a PII; D C F T |
| Alta/revisión de beneficiarios y catálogos/RFC/centros/proveedores | `/beneficiarios/altas/nueva`, `/beneficiarios/altas/{request_id}/final/{action}`, `/admin/proveedores-clientes`, `/admin/rfc` | canonical onboarding/catalog services → beneficiary draft/review/decision/catalog tools | empresa/beneficiario/ruta/aprobador; R/W; destino bancario exacto; D C F T |
| Registro deportivo: ingesta/archivos/revisión/editar/adoptar/rechazar/reprocesar/commit | `/api/registration-review`, `/api/registration-review/{session_id}/canonical-adopt`, `/api/registration-review/{session_id}/commit` | dashboard review owner, CTT governed services → registration review tools, commit separado | roles actuales coordinador/finanzas/admin/superadmin (aliases), además alcance por torneo pendiente; sesión/draft/versión; R/W; menores minimizados; D C F T |
| Equipos/jugadores: consultar/editar/verificar/eliminar | `/teams`, `/players`, `/api/team/{team_id}/edit`, `/api/player/{player_id}/verify` | dashboard Team/Player route owner → team/player focused tools | torneo/equipo/jugador y autoridad específica; R/W; incidentes y linaje; D C F T |
| Torneos/SOUL/compromisos/contratos/expedientes/operaciones | `/admin/torneos`, `/admin/sports/soul-wizard`, `/api/assistant/admin/tournaments/{tournament_id}/contract-drafts/{draft_id}/apply` | tournament/operations canonical owners; `operations.*` → dossier/commitment/contract/SOUL tools | torneo/cartera/posición/versión; R/W; cobertura/faltantes; D C T |
| Comunicaciones/media/invitaciones/campañas/calendarios | `/api/assistant/admin/email/campaigns/send`, `/api/assistant/admin/invitations`, `/api/assistant/conversations/{conversation_id}/media` | `communications.*`, operations media/reminders, route services → prepare/send/schedule/cancel/media tools | facultad/destinatarios/torneo; W externo separado; destino/preview/recibo; D C F T |
| Dirección: tableros/reportes publicados y gestión interna de reportes | `/direccion/tableros`, `/direccion/reportes`, `/direccion/reportes/gestion/borradores/{draft_id}/{target}` | client_executive/reporting route owners → direction read/report-config/draft/transition tools | posición interna elegible + cartera/torneo activos; superadmin supervisión; R/W separados; admin no acceso global; D C T |
| Assistant: conversaciones/casos/contexto/archivos/RAG/reportes/configuración | `/api/assistant/conversations`, `/api/assistant/rag/search`, `/api/assistant/rag/config`, `/api/assistant/reports/export` | canonical assistant modules → conversation/case/search/report focused tools | empleado/conversación/artefacto; R/W; #445 contexto pendiente, no llamar dispatcher libre; D C F T |
| Inbox/artefactos/soporte/telemetría/customer success | `/admin/sam-inbox`, `/admin/artifacts`, `/soporte`, `/admin/customer-success/uso` | sam_inbox/artifacts/support route owners → queue/artifact/support/report tools | actor/objeto/empresa/facultad; R/W donde ticket/comentario; índice no archivo universal; D C T |
| Administración/accesos/posiciones/identidad/delegación | `/admin/perfiles`, `/admin/empleados`, `/admin/puestos-autorizacion/{position_key}`, `/admin/identidad/cambiar` | auth/access/project authorization services → review/configure tools o handoff seguro | facultad administrativa actual; W sensible; no ampliar permisos desde token ni suplantar; D C T |
| Login/password/credenciales, webhooks, salud/static/redirects, training/synthetic-data | `/login`, `/panel/cambiar-contrasena`, `/admin/gastos/sat/credentials`, `/healthz`, `/ingress/tocino-webhook` | owning handlers; **handoff seguro / infraestructura**, no tool genérica | cubiertos en inventario; secretos fuera de chat, ingress no acción de usuario; determinar equivalencia funcional sin exponer secretos ni labores de producción |

## Decisiones precisas para el padre (sin detener trabajo independiente)

1. **Instalación y distribución privada.** El objetivo indicado es conexión
   personal con cuenta de paga y uso web/Android/Windows, sin asumir Enterprise.
   La ruta documentada es registrar la app MCP privada antes de empaquetar su
   referencia. Su disponibilidad concreta por cuenta/cliente sigue pendiente de
   validación autorizada; no prometer elegibilidad universal por plan o Free.
   No se registra ni inventa app ID, endpoint ni grant en este trabajo.
2. **Frontera multiempresa.** `tenant_id` del contrato Agent Action no prueba una
   organización real. Definir mapping canónico instalación → organización →
   empleado → razones sociales → cartera/torneo; negar ambigüedad y no asumir que
   RFC, tenant Supabase y cartera son la misma entidad. Confirmar si cada conexión
   queda vinculada a una organización o si el usuario puede elegir entre varias
   mediante selección validada por servidor. El perímetro local usa una
   organización por identidad como contrato conservador, NO como decisión canónica.
3. **IdP/OAuth.** Elegir servidor de autorización que enlace cada cuenta SamChat
   al empleado vigente. Los actuales cookie/bridge Supabase/Telegram no son un
   OAuth grant intercambiable. No implementé emisión de credenciales ni grants.
4. **Persistencia/commit.** El dueño del dominio debe unir reserva idempotente,
   consumo de confirmación humana, estado de negocio y auditoría en transacción
   o outbox/reconciliación durable. Falta adaptador productivo probado; SQLite
   de tests no lo resuelve. Nunca reintentar automáticamente un efecto incierto.

## Gaps que impiden habilitar capacidades

- **UI_SOURCE_UNAVAILABLE:** frontend activo no versionado aquí; archive distinto
  del build, investigación del padre. Sin server/SSH. Los HTML/redirects y API
  encontrados NO permiten afirmar cobertura de controles UI ni roles reales.
- **CANONICAL_SCOPE_UNPROVEN:** heredado de Agent Action v0.1. Las 44 acciones son
  dueños candidatos, no prueban autorización por empleado/objeto/empresa. No se
  envuelven directamente ni se reimplementa SQL/reglas del dominio.
- **REGISTRATION_SCOPE_UNPROVEN:** el guard de revisión acepta roles de sesión;
  el commit localiza sesión por UUID y aplica locks/revalidación. No demuestra
  por sí solo scope OAuth/empresa/torneo. No emular cookies para eludirlo. Extraer
  servicio canónico con dueño de dashboard o adaptador autorizado en tarea futura.
- **LEGACY_ROSTER_PROHIBITED:** `tools.py:tournament_team_register_from_roster`
  elige categoría inicial y completa nacimiento/teléfono. También existe fallback
  de nacimiento en `router.py`. No se importa ni se envuelve. Adopción de campos
  canónicos sólo modifica draft; commit separado recalcula roster, fotos,
  incidentes, faltantes y duplicados. Reusar el rollout shadow, no activarlo.
- **PR445_INTEGRATED:** `6cd122f` incorporado en esta rama aislada conservando
  `120a411`; matriz regenerada y regresiones puras ejecutadas. No se modificaron
  sus archivos ni duplicaron sus correcciones. La integración no prueba autoridad
  nueva para los 44 adapters.
- **PERSISTENCE_UNPROVEN:** no auditoría/idempotencia productivas del plugin,
  migración, transaction adapter ni prueba de crash de negocio. Un replay puro
  exacto no prueba once-only commit. Nuevas capturas no son posibles en esta base.
- **TOOL_SCHEMA_PENDING:** schemas específicos entrada/salida, límites por tool,
  anotaciones MCP, cursor scoped, recursos/descargas autorizadas, postcondiciones
  y pruebas por handler pendientes. CSV no es catálogo operativo MCP.
- **PACKAGE_TRANSPORT_PENDING:** skill `plugin-creator` ausente del catálogo
  cloud (todas las páginas), executor y búsqueda de SKILL.md en ubicaciones
  locales disponibles. Sin script/validador oficial; no se crea scaffold
  instalable. Hay fábrica SDK inerte; no transporte HTTP, servidor remoto
  ni plugin conectado. La documentación permite empaquetado manual, pero
  no se inventó el registro MCP ni su ID antes de la decisión del padre.
- **STATIC_LIMITATIONS:** AST cubre las declaraciones Python raíz/`src/`, los
  includes/mounts quedan inventariados; no resuelve efectos transitivos, middleware,
  pruebas dinámicas ni fuentes JS/nested apps. API genérica `src/devnous/api.py`
  mencionada en canon no existe en este checkout: no afirmar su paridad. No
  confundir plan contractual FMF/calendario/logística/sponsors con función existente.

## Contrato OAuth/MCP y distribución consultado

Consultado 2026-10-01, fuentes oficiales:

- [Quickstart](https://developers.openai.com/plugins/quickstart): conexión personal
  y tools enfocadas; UI opcional.
- [Autenticación](https://developers.openai.com/plugins/build/auth): OAuth 2.1,
  authorization code con PKCE, metadata del recurso protegido y del issuer,
  audience/resource, validación en cada request. Elegir CIMD/DCR/cliente
  predefinido con el IdP; copiar callback real del registro futuro, no adivinarlo.
- [Empaquetado](https://developers.openai.com/plugins/build/plugins): manifest
  portable `plugin.json`; scaffold de plugin-creator conserva layout compatible.
  Mapping de un MCP registrado no debe inventarse antes de registrarlo.
- [Conexión y pruebas](https://developers.openai.com/plugins/deploy/connect-chatgpt):
  HTTPS Streamable HTTP o Secure MCP Tunnel para developer mode. La cuenta y la
  política del workspace condicionan disponibilidad. Conectar/instalar/publicar
  quedan fuera de esta autorización. No se abrió túnel ni endpoint.

Un futuro adaptador de transporte toma el bearer exclusivamente del canal de
seguridad, no argumentos del modelo; valida issuer/audience/revocación/expiración
por intento, resuelve empleado y consulta facultades actuales. OAuth scopes
restringen: nunca conceden acceso a una fila, aprobación o empresa. Ni una
confirmación del LLM ni `approved=true` sustituyen la aprobación humana vinculada.

## Condiciones de cierre por capacidad

Por cada fila: confirmar fuente UI → identificar owner canónico → documentar
perfil/facultad/objeto → entrada/salida acotada → lectura pura o preview/commit →
authorization negativa/positiva real con fixtures → auditoría y dedup en mismo
límite transaccional → verificar resultado y aislamiento → habilitación revisada.
Las transacciones financieras no deben cambiar política COI ni gates; aprobar
un documento, cerrar Payment Run y registrar evidencia de pago son actos distintos.
El cierre total exige todas las funciones del perfil, no sólo las 51 candidatas.

**Canon unchanged:** se añade inventario y contratos locales deshabilitados que
preservan invariantes, sin nuevas reglas contables, concesión de autoridad,
rutas activas, despliegue o afirmación de aceptación. Los tres hashes coinciden
con el registro. No se editaron canons, PR445, credenciales ni trabajo ajeno.
