# Preparación de bindings y wire — 2026-10-29

Estado: propuesta de integración, **no instalada ni autenticada**. Complementa
el candidato financiero `coded_not_wired` del PR #514; no declara Batiseñal E2E.
Runtime objetivo: web SamChat y sus owners PostgreSQL. Código/plataforma conserva
un owner técnico distinto; ninguna facultad financiera concede acceso a código.

## Reutilización de identidad existente

`private_plugin/oauth_identity.py:OAuthIdentityProvider.resolve_current` ya
verifica firma, issuer, audience, cliente, tiempos, scopes restrictivos y los
registros actuales de grant, link y empleado. `private_plugin/local_reads.py`
revalida contexto, instalación, organización, perfil y revocación antes/después
de leer. No se debe crear un segundo verificador JWT ni aprovisionar empleados.
El servidor local sólo registra get_profile y las dos lecturas de Dirección;
no registra los datasets ampliados.

La composición `readonly_gateway/canonical_authority.py` recibe un
`ServerBinding` desde un proveedor confiable, pero no conecta ese proveedor al
transporte. Los tests nativos usan un binding sintético. Preparar el enlace exige:

| Frontera | Fuente confiable y condición |
| --- | --- |
| Conexión | `current_connection()` del transporte autenticado; nunca argumentos del modelo |
| Empleado | `Identity.actor_id` resuelto por el link existente, UUID válido y activo |
| Instalación/organización | Identidad verificada y selección de datasource configurada por el servidor; rechazar mezcla de instalaciones u organizaciones |
| Binding/generación | Identificador opaco y generación que vinculen grant, link/perfil, organización, instalación, empleado y versión de política; cambio/revocación invalida respuesta y cursor |
| Expiración | Límite efectivo token/grant, convertido a datetime UTC consciente de zona; no extenderlo |
| Ceilings | Owner de objetos/torneo/versión y contexto fiscal del servidor; los scopes OAuth sólo restringen, no generan DomainScope |
| Revalidación | Resolver identidad actual en cada intento y cada revalidación; autoridad financiera en transacción nueva READ COMMITTED, separada del snapshot de datos |

`ServerBinding` no tiene campos explícitos de organización, perfil o grant.
Por eso copiar sólo empleado e instalación pierde dimensiones de autoridad.
Antes de implementar se necesita definir, revisar y probar su vinculación en el
identificador/generación y en la selección de datasource/política. Denegar
ambigüedad, ausencia y discrepancia; no inferir scopes desde email ni rol aislado.

## Auditoría y aceptación autenticada

Los protocolos tampoco son intercambiables: `private_plugin/local_reads.py`
requiere un recibo durable (`append`/`append_read`), mientras
`readonly_gateway/contracts.py:AuditSink.append(event)` recibe metadatos y no
retorna recibo. Se requiere composición explícita con persistencia comprobada,
minimización, correlación de resultado/scope y fallo cerrado fuera de la base de
negocio. `MemoryAudit` únicamente prueba el flujo de tests.

La siguiente aceptación necesita una conexión de prueba ya autorizada y sus
registros existentes, sin crear credenciales aquí. Debe demostrar grant válido,
revocado/expirado, cambio de link/organización, objeto ajeno, revocación mientras
se lee, fallo de auditoría y ausencia de respuesta/datos en cada denegación.
Los 17 casos PostgreSQL nativos no satisfacen esa identidad ni la durabilidad.

## Wire de desarrollo y Unified Resources

Los schemas anunciados por Agentius Directo coinciden con la incompatibilidad
documentada en la revisión: create requiere base_sha, manifest_hash, request_id
y scopes read/write separados; start/resume requiere expected_contract_hash;
artifact requiere artifact_id. El contrato interno usa revisión entera, una
lista única de archivos y hash de contenido. Esos valores no son equivalentes.

No se ejecutó work_create/start, ni se probó el worker remoto. La preparación
mínima es obtener mediante lectura ya autorizada el árbol/manifest del proyecto
y recibos de un trabajo existente; acordar mapeo de estados, idempotencia y
proveniencia de artefactos; probar contra el proxy vigente que la autoridad no
se amplía. No sintetizar hashes, IDs de artefacto ni request_id persistente a
partir de una revisión local. No habilitar herramientas desde este documento.

El antecedente Unified Resources del 18/oct continúa sin artefacto accesible
verificado; las herramientas disponibles no anuncian `resources_*`. Se conserva
su hash y estado REVIEW_ONLY_NOT_INSTALLED en la revisión principal. No se
duplica ni reconstruye un adaptador cuyo contrato no pudo verificarse.

Canon unchanged: este plan identifica contratos existentes y evidencia pendiente;
no cambia owners, reglas, permisos, despliegue ni criterios de aceptación.
