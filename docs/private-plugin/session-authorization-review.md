# Autorización sobre SamChat actual: revisión local 2026-10-01

Runtime: `copa_telmex_dashboard.py`. Dominio: identidad web/empleados de Gastos, autoridad Direction y perímetro privado MCP. Datos canónicos: Postgres para sesión/empleado y autoridades inspeccionadas; no se usa el puente Supabase. Nivel: `coded_not_wired`, fixtures locales, sin cuentas, claves o grants reales. Canon unchanged: se conservan autenticación, facultades y políticas existentes; no hay cambio al runtime ni a los tres RFC/razones sociales.

## Recomendación concreta

Añadir, sujeto a revisión antes de conectar, autorización OAuth del plugin **sobre el login actual**, sin nuevo proveedor de identidad ni otra contraseña. El usuario entra a SamChat, ve el cliente y alcances solicitados, acepta explícitamente y se vincula su ID interno existente a un perfil opaco. El plugin no recibe contraseña ni cookie de SamChat. Cada llamada conserva recarga de empleado, vigencia/revocación del grant y guardas canónicas por objeto.

Evidencia:

- `copa_telmex_dashboard.py:1774`: SessionMiddleware `samchat_session`, 24h, SameSite lax, secure según APP_URL.
- `src/devnous/gastos/routes/auth_routes.py:709`: login canónico, consulta empleado existente, contraseña bcrypt y activo; `:849` guarda `empleado_id`. No extender aquí con autoalta/reactivación/promoción.
- `src/devnous/gastos/routes/dependencies.py:209`: `get_current_empleado` vuelve a cargar empleado por UUID y rechaza ausente/inactivo. Se reutiliza su identidad; `can_access_path=True` por fallos de permisos en `:285` NO concede autoridad de negocio al plugin.
- `auth_routes.py:1064`: cambio de identidad administrativa guarda `impersonator_empleado_id`. El consentimiento del plugin rechaza suplantación aunque sea superadmin: sólo vinculación de cuenta propia.
- Logout limpia cookie en `auth_routes.py:919`; no hay registro de revocación de sesiones/grants demostrado. Logout de navegador y desconexión del plugin deben seguir explícitamente diferenciados hasta definir su política; no asumir revocación global.

Riesgo: operar OAuth sobre SamChat añade una superficie de autorización nueva aunque preserve el login. Requiere almacenamiento transaccional de consentimiento/códigos/grants/revocación, expiración, consumo único, protección CSRF, callback/clientes preautorizados, límites de solicitudes y claves dedicadas. No basta publicar rutas o convertir la cookie web en bearer.

## Unidad de código local

`authorization.py` contiene funciones sin rutas ni storage:

- metadata candidata de resource/authorization-server; sólo authorization_code, PKCE S256 y cliente explícito. No anuncia refresh, DCR o CIMD no implementados;
- validación exacta de cliente, callback, resource y scopes de lectura; rechaza parámetros duplicados/desconocidos, identidad inyectada, wildcard y downgrade a plain;
- adaptación a `get_current_empleado`, sin leer contraseñas/cookies crudas ni aceptar actor del modelo;
- consentimiento ligado a borrador server-side, cuenta actual, browser binding, CSRF, vencimiento y fingerprint del request completo;
- comprobación de verifier PKCE y binding de intercambio. **No consume un código ni emite tokens**: el host debe aportar consumo único/expiración/revocación/auditoría atómicos. Estas funciones no constituyen un servidor OAuth completo.

Pruebas usan la función canónica `get_current_empleado` cargada del código fuente con dependencias sintéticas; verifican ausencia/inactividad/suplantación y cuenta estable. No ejecutan login ni reciben credenciales. El AST loader vive sólo en tests; no es un dispatcher del plugin.

La [documentación oficial de OpenAI](https://developers.openai.com/plugins/build/auth) permite un authorization server propio y exige discovery, authorization-code PKCE S256, audiencia/resource y verificación por solicitud. El callback exacto se obtiene al preparar la conexión; no se adivina ni se registra ahora. El prototipo sólo declara metadata candidata y no demuestra conectividad ni distribución.

## Auditoría canónica investigada

| Propietario | Evidencia | Reutilización / límite |
|---|---|---|
| Agent Action receipts | `database/migrations/20260919_agent_action_receipts_v01.sql`, `src/samchat/agent_actions/receipts.py` | Contrato más próximo; migración y memoria, sin adapter productivo probado. Candidato preferido para adaptar después de demostrar su almacenamiento, no prueba actual de persistencia. |
| Customer Success | `src/devnous/gastos/services/customer_success_audit.py:128` | Helper ejecuta ensure-schema, retorna None, captura errores y rollback. No satisface receipt durable fail-closed. No envolverlo fingiendo éxito. |
| AccountingAuditLog | `src/devnous/gastos/models.py:796` | Participa en transacción del caller; mantenerlo para su objeto/acción contable, no inventar objetos para lecturas MCP. |
| Budget/AR/reporting | `src/samchat/budgets/service.py:5224`, `src/samchat/ar/collection_matches.py:412`, `src/samchat/client_reporting/service.py:46` | Auditoría propia de cada dominio; conservar en la mutación canónica. No equivale a log genérico de plugin. |
| AssistantRun | `src/devnous/gastos/models.py:2810` | Requiere conversación y almacena contenido; no sustituir recibo mínimo por prompts/payloads personales. |

`documento_workflow_service.py:906` confirma documento/aprobación antes del evento Customer Success en `:960`; ese evento adicional no es atómico con la transición. No modificar política contable ni helper global en este trabajo. SQLite es únicamente evidencia local de contrato; no se propone como almacén productivo aceptado.

## Cola priorizada de adaptadores y omisiones

1. `direction_list_scopes`: guard `_assigned_direction_portfolios` (`client_executive_routes.py:59`) y `home.resolve_scope:117`; sólo IDs/nombres operativos, nunca faculta nuevas carteras. Implementado en composición local: tool sin argumentos con schema estricto, máximo 1000 carteras/torneos y 64KB, IDs únicos y etiquetas limitadas; llama guards/resolver antes y después y no construye el reporte. Bloquea en lugar de truncar resultados mayores; paginación futura sigue pendiente.
2. Documentos propios: filtro canónico `user_routes.py:29114`; extraer read model del HTML y probar razón social/objeto antes de tool. No usar el adapter general de gastos sin prueba de scope.
3. Reportes publicados: ruta `client_executive_routes.py:185` filtra cartera pero los snapshots necesitan revalidar torneos/fuentes actualmente denegados. Bloqueados hasta proyección segura.
4. Gastos/archivos/revisión deportiva: requieren read owners puros, límites de archivos y autoridad por registro; siguen en matriz completa. `GET registration-review` (`copa_telmex_dashboard.py:4624`) devuelve OCR bruto y usa rol de sesión sin demostrar guard por registro; no envolver.
5. Escrituras de cada familia: mantener servicio canónico, preview/confirmación/versiones, idempotencia y receipt transaccional antes de habilitar.

Hallazgos que impiden confundir GET con read puro:

- `documento_payment_service.py:657` (`get_pending_document_payment_overview`) llama promoción de solicitudes en `:673`; SamInbox lo usa en `service.py:544`. No habilitar `receipts.pending_payment_overview` como lectura. Puede aprobar/commit/notificar por `documento_workflow_service.py:362`.
- `budgets/service.py:5260` (`list_budget_audit_events`) llama `ensure_budget_schema`.
- Detalle de gasto en `user_routes.py:26900` también tiene ensure-schema y autoridad amplia que no demuestra aislamiento fiscal.

## Límite de la siguiente aprobación productiva

No se requiere pedir un issuer técnico al usuario. La opción recomendada es **SamChat actual como autoridad OAuth, usando su login y sólo empleados existentes**. Antes de conectar, presentar la implementación revisada de almacenamiento/consentimiento/revocación y el cliente/callback concreto para aprobar esa superficie. Falta evidencia canónica empleado→razón social→objeto en varias familias: se resuelve técnicamente por servicio; no pedir un catálogo nuevo de roles ni permiso genérico para saltar guardas. Toda función no demostrada permanece deshabilitada.

## Revisión adversarial independiente

Hallazgos reproducidos con fixtures y corregidos en esta unidad:

- Cambio de rol/denegación de fuente durante un read: revalidar empleado, asignaciones, cartera/torneo, organización y fuentes después del await del read; suprimir resultado si difieren.
- Reasignación del vínculo OAuth: el grant queda ligado además a employee_id/organization_id/profile_id originales. Cambiar el vínculo no transforma un token existente en acceso de otro usuario.
- Snapshot SQLite retenido: rechazar transacción activa ajena en el adapter de identidad; no devolver revocación obsoleta ni alterar una transacción del caller.
- INSERT de auditoría ignorado o alterado por trigger: exigir una fila afectada y readback exacto antes de COMMIT/ID.
- Parámetros OAuth duplicados ocultos por QueryParams: aceptar únicamente pares explícitos de strings, conservando duplicados; el host debe usar `multi_items()` y no `dict(request.query_params)`.
- Challenge PKCE/registro malformado: falla cerrada y error estable, sin TypeError con detalles internos.

Estas correcciones se limitan al paquete del plugin y pruebas. No afirman serialización productiva ante cambios concurrentes de política: la composición real debe garantizar lectura actual y frontera transaccional/cancelación adecuada; SQLite y dobles de negocio no prueban esa frontera.

## Evidencia final de esta unidad

- Suite privada final: **110 tests PASS** con `python scripts/private_plugin/run_offline_tests.py`.
- PR445: **40 tests PASS** con runner de regresiones puras; no es su suite completa.
- Cobertura del paquete: **96% (849/880 statements)** en ejecución instrumentada; el test adicional final de límites de list_scopes también pasa en la suite final. Autorización 99%, identidad OAuth 98%, SQLite audit97%, Direction93%.
- Black/isort/flake8 y `git diff --check` PASS. Hashes de los tres canons coinciden con el registro.
- Sin red de negocio/modelos, secretos, grants reales, schema productivo, listener, push, PR o despliegue. Fábrica original inerte; composición local expone tres lecturas, sin mutaciones.
