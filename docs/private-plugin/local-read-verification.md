# Roundtrips MCP locales después de PR445

2026-10-01. Runtime futuro: `copa_telmex_dashboard.py`. Dominio de esta unidad:
perfil conectado y lectura de Dirección. Persistencia canónica del negocio:
Postgres con fuentes híbridas; esta composición no abre ninguna conexión.
Nivel de evidencia: **código y fixtures locales**, no producto conectado.

Se integró el commit de negocio `6cd122f1030ab39631d77e706a9f5a1a7192231d`
mediante merge exclusivamente en la rama aislada `feat/private-plugin-review-20261001`.
`120a4117925e` continúa siendo ancestro: no se reescribió ni descartó. Ningún
archivo de PR445 recibió correcciones nuevas en esta unidad. Matriz regenerada:
404 fuentes, 561 declaraciones, 627 paths con alias, 44 acciones, 36 AccessTool.
Los hashes de los tres canons siguen coincidiendo con el registro.

## Qué ejecuta ahora el código local

`create_local_read_server` construye un objeto del SDK MCP con dependencias
explícitas. No lee configuración, no monta rutas, no inicia un listener ni abre
túneles. La fábrica original `mcp_boundary.create_server` continúa con discovery
vacío; no existe flag que habilite el negocio productivo.

| Tool local | Dueño/contrato utilizado | Prueba y límite |
| --- | --- | --- |
| `get_profile` | `IdentityProvider.resolve_current`, cuenta vinculada en servidor | Entrada vacía, respuesta única `{id}` opaca, metadata `openai/profile`, schema estricto; fixtures de reconexión/aislamiento/revocación. El vínculo persistente real aún no existe. |
| `direction_read_summary` | `_assigned_direction_portfolios`, `_direction_source_access`, `_is_superadmin`; `home.resolve_scope` y `home.build_home` | Las pruebas ejecutan esos cuerpos canónicos con fuentes inferiores sintéticas. Requiere `direction:read`, empleado activo coincidente, puesto/alcance canónico y mapping acreditado a organización antes de leer. |

El scope OAuth propuesto es una restricción adicional, no una facultad. El
catálogo local puede mostrar la lectura a una conexión con ese scope, pero la
llamada sigue denegada sin posición/cartera/torneo actuales. No acepta actor,
organización, roles, permisos, bearer, SQL ni función a despachar desde el LLM.
Se reautentica al iniciar y después de la lectura; un cambio de conexión o
revocación impide liberar el resultado. El año y el alcance de la respuesta se
comparan con el solicitado/autorizado.

Dirección entrega indicadores agregados con valor decimal textual o `null`,
fuente, fórmula, corte, cobertura y brechas; omite filas de pagos, contactos y
rosters. Conserva las denegaciones específicas de Finanzas y Presupuestos. Los
campos desconocidos no se reenvían y los tipos/tamaños inválidos bloquean la
respuesta. No calcula reglas financieras nuevas ni convierte aprobación en pago.
El builder declara sus cortes independientes; no se promete atomicidad entre fuentes.

El recibo de lectura liga hashes del resultado exacto y del ámbito autorizado.
Si `append_read` falla o no devuelve ID, se suprime el resultado. Es contrato de
auditoría inyectado: los fixtures no prueban durabilidad productiva. Confirmación,
idempotencia de escrituras, adopción deportiva y capturas continúan como en el
hito inicial; ninguna mutación de negocio se habilitó.

## Pruebas

Con las dependencias de `requirements-private-plugin-test.txt`, Python 3.12:

```sh
python scripts/private_plugin/run_offline_tests.py
python scripts/private_plugin/run_pr445_regressions.py
```

El runner protege contra sockets, procesos ajenos al inventario Git y lectura
de archivos de entorno/credenciales. El segundo ejecuta el archivo existente
`test_assistant_financial_claims.py` de PR445: **40 regresiones aprobadas** sobre
fuentes/cifras, folios, contexto y ausencia de ampliación de autoridad. No es la
suite completa de PR445, provider transport ni integración productiva. Las
pruebas nuevas de Dirección cargan sólo funciones canónicas seleccionadas desde
AST para evitar bootstrap; no prueban el import/montaje productivo completo.

Resultado de cierre: **64 pruebas locales aprobadas**, más las **40 regresiones
de PR445**, **104 en total**. Cobertura de sentencias del paquete privado: **95%
(438/459)**, incluyendo Direction (93%) y composición de lecturas (91%). Black,
isort, flake8 y `git diff --check` pasan. No se amplía esta evidencia a la suite
completa del repositorio, la UI o persistencia productiva.

Pruebas de roundtrip incluyen initialize/discovery, schemas de entrada/salida,
perfil estable, scopes, rol/posición negada, usuario/organización/torneo ajenos,
fuente denegada, revocación durante lectura, cambio de año/alcance, auditoría
fallida, errores sanitizados y lectura sin escrituras de negocio.

## Decisiones ya resueltas y la pregunta indispensable

El [mapa de identidad/autoridad](identity-authority-map.md) distingue las reglas
existentes de las integraciones ausentes. No hay que volver a decidir roles,
cuenta propia o autoridad de Dirección. No se usará el bridge Supabase para el
plugin: crea/reactiva/promueve empleados. Tampoco se envolverán fallbacks
permisivos de helpers genéricos como autorización universal.

Para el padre, antes de registrar acceso persistente: **¿la primera conexión
privada queda limitada a la instalación actual de Plataforma Sports y sus
cuentas existentes, o debe incluir varias instalaciones desde el inicio?**
Esto define qué mapping autorizado se debe implementar; no presupone que todos
los usuarios puedan ver todos los RFC de la instalación. No se requiere una
respuesta para seguir construyendo tests locales.

Después harán falta revisión concreta del vínculo OAuth, propietario del IdP,
mapping por organización/objeto y recibos persistentes en el dueño transaccional.
No son motivos para inventar tenants, credenciales o grants. No se pide secreto
alguno y esta entrega no solicita autorización de instalación/despliegue.

## Distribución y eventos: límites comprobados

La [guía de conexión](https://developers.openai.com/plugins/quickstart) describe
una conexión personal en ChatGPT Work web. El target es la cuenta personal de
paga indicada, sin asumir un workspace Enterprise. La elegibilidad de esa cuenta
y el uso en Android/Windows/Surface se validarán con una conexión autorizada;
no están probados por estos tests. Voz tampoco está probada.

La [guía de gestión](https://learn.chatgpt.com/docs/enterprise/plugin-management)
advierte que declarar MCP en `mcp.json` de un plugin importado puede limitarlo
al escritorio, incluso con HTTPS. Para referenciar una app existente desde
`.app.json` se usa el ID de app (`asdk_app_`, `connector_` o `templated_apps_`),
no el ID `plugin_`. Esa guía no demuestra elegibilidad de cuentas personales.
No se generó ningún ID o marketplace ni se registró una app. El scaffold de
`plugin-creator` continúa pendiente porque la skill no está disponible.

La [autenticación oficial](https://developers.openai.com/plugins/build/auth)
exige validar credenciales y resolver el perfil desde ellas; declarar metadata
no implementa OAuth. Faltan HTTP/TLS, protected-resource/issuer metadata,
code+PKCE S256, validación criptográfica y vínculo persistente sin provisión de
usuarios. El `IdentityProvider` actual es un puerto de integración, no un
verificador de tokens implementado.

[MCP Events](https://developers.openai.com/plugins/build/mcp-events) requiere
protocolo `2026-07-28`. El SDK instalado `mcp==1.29.0` declara `2025-11-25`.
No se anuncian eventos ni `server/discover` 2.0, y no se implementa un protocolo
alternativo improvisado. Antes de esa unidad se deberá elegir SDK compatible y
probar propiedad, permisos, filtros, expiración, dedup, firma y defensa SSRF de
callbacks. No hay suscripción, callback, secreto ni entrega real. Archivos y sus
metadatos de transporte siguen siendo unidad pendiente, igual que la paridad
UI/API completa; estas dos tools locales no reducen el objetivo final.

**Canon unchanged:** integración local de lecturas y evidencia, sin cambio de
autoridad, contabilidad, usuarios, persistencia productiva, distribución ni
despliegue. No push, PR ni merge a main.
