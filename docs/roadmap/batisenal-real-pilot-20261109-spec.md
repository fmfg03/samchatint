# Batiseñal — especificación del primer piloto con Chatty real

Fecha: 2026-11-09. Estado: **aprobada expresamente el 2026-11-10 mediante «aprobada»; construcción en curso**.

La aprobación incluye los textos literales de §12. SHA256 del documento revisado: `0b8d4aa18cb9aa96d66f1dd048ff5fcb53830377fe8f4f756c23e4fbf7926561`. Se conserva a continuación la propuesta revisada; las frases de solicitud de aprobación describen aquel checkpoint y no anulan la aprobación registrada. Instalación accesible, publicación Git y aceptación real siguen fuera de esta aprobación.

Historia vigente: `story.md`, aprobada con «Aprobar historia de la primera entrega», recibo `story-approval.json`. Base de código: main `0f0ca09876ac76e8d808c872feb4436fc2a207a1`, árbol `497a95b62879da5ee04a331b18c73d9ecad42d1e`. Los tres canons se leyeron en orden y sus hashes coinciden con el registro. Fuentes y observación del runtime: `research.md` y `research-evidence.json`; contrato de referencia: `spec-v01-reference.txt`.

## 1. Resultado y límites de esta aprobación

Construir una composición **aislada y apagada por defecto** que reutilice los owners de SamChat y permita, una vez configurada y autorizada su instalación de prueba, este recorrido: login individual auténtico → consentimiento del único caso sintético → lectura desde Chatty real → propuesta privada → recibo tras commit → revisión privada en navegador. El caso y su INFORME son sintéticos; la persona se autentica realmente en la instalación de prueba. No se usa un login de fixture para acreditar esa conexión.

Aprobar esta especificación autoriza construir el código, sus pruebas y un ensayo local aislado con identidades técnicas claramente identificadas como pruebas. **No autoriza** instalar un servicio accesible, cambiar proxy/DNS/red, registrar un MCP en ChatGPT, crear credenciales reales, provisionar una identidad o permisos reales, iniciar consentimiento por la persona, aplicar SQL a la instalación financiera, activar producción, publicar respuestas ni fusionar/desplegar un PR nuevo. El plan operativo posterior presentará esos objetos y efectos exactos cuando sean revisables.

Se puede cerrar «código apagado y validado» por separado; eso deja A1/A2 y la aceptación real pendientes. El piloto sólo se declara aceptado cuando la matriz completa de la historia se acredita sobre el mismo release, cliente, esquema y configuración. Ningún pase del PR516, stub o documentación del cliente sustituye ese recorrido.

## 2. Enfoque de implementación

### 2.1 Reutilización y ensamblaje aislado propuesto

La arquitectura que se somete a aprobación es un **entrypoint FastAPI mínimo de prueba**, dentro del repositorio, separado del proceso financiero. Reutiliza los handlers y rutas nativos GET/POST `/login` y `/logout` de `auth_routes.router`, `get_current_empleado`, el mecanismo de sesión firmado, los owners de soporte y `mount_pilot`; no crea otro sistema de tickets, autenticación ni una base de producto paralela. El router de auth también incluye `/admin/identidad` y cambio/restauración de identidad: esos endpoints no se montan en el piloto. Se seleccionan únicamente las rutas nativas de login/logout, sin duplicar su implementación ni añadir una identidad alternativa.

No importar `copa_telmex_dashboard.app` para esta composición: su importación establece engines globales y su startup ejecuta DDL/guardas, purgas de uploads y recuperación de jobs SAT ajenos al piloto. Tampoco llamar a setters de sesión de un proceso productivo ya iniciado. La nueva fábrica corre en un proceso propio, acepta una sola composición por proceso y configura los setters canónicos de auth/dependencies antes de montar rutas. Una segunda composición incompatible o la presencia del entrypoint financiero en ese proceso se rechaza.

La fábrica valida localmente primero un manifiesto cerrado y sólo puede construir conexiones para un destino previamente identificado como aislado. El ensayo de construcción usa un clúster PostgreSQL desechable Unix-only, sin DSN externos. La futura instalación de prueba utiliza una instancia o base exclusivamente sintética, nunca la misma base financiera ni su `public`; reutiliza código y owners lógicos, no registros o credenciales de esa base. Ese recurso de prueba no es otra base de producto operativo. La infraestructura concreta sigue pendiente de su plan y autorización. Instala `SyntheticProtocol` antes del primer SQL de los engines de negocio/OAuth que lo necesitan. El `search_path` está fijado al esquema `bs_test_v01_<32hex>` del manifiesto y a los catálogos necesarios; no incluye `public` como fallback. Una conexión con esquema, owner, rol, instalación o binding distintos falla cerrada. El rol de servicio no es superusuario, propietario ni miembro del rol owner y no tiene acceso a esquemas de negocio ajenos. Verificar privilegios efectivos, incluidos PUBLIC y herencia; no basta revisar GRANT nominal.

El código del piloto no altera la configuración de `samchat-gastos.service` ni el gateway Agentius. Main continúa sin montar Batiseñal en el servicio financiero. La futura instancia de prueba reutiliza una cuenta legítima de **esa instalación**, con credencial enrolada por su owner: no copia cookies, SECRET_KEY, passwords, hashes de contraseña ni perfiles de producción. El acceso administrativo al host no crea autoridad de negocio.

### 2.2 Portado controlado del candidato

Usar como fuente el ZIP histórico SHA256 `b8d89c6d0b1c7f04b48cce0de6e54827740ac1e70c53efa657ed094a21f46b63`, más los overlays y parches modernos pinneados de #516. Prepararlo en un directorio nuevo mediante sus verificadores; conservar ZIP/manifiestos/fixtures intactos. Portar sólo los módulos de la allowlist (§9), reconciliándolos con main. No copiar el checkout histórico de 586 archivos ni reemplazar modelos o rutas actuales por sus versiones antiguas.

Reutilizar el **CaseBridge v0.1 de tres herramientas**, no el Bridge general antiguo de cinco operaciones. `batisenal_bridge.py` sólo se porta como dependencia reutilizada; sus herramientas de listado/publicación general no se exponen en el recurso del piloto. Conservar el framing moderno, sus comprobaciones de metadata y la política de referrer de formularios revisada. No importar código desde `tests/fixtures` en runtime.

En `private_plugin` el delta previsto se limita a las extensiones necesarias de `oauth_flow.py` y `oauth_store.py`. El modo normal conserva sus scopes `:read` y continúa rechazando soporte/escrituras. El modo de soporte exige explícitamente `SupportAuthorizationConfig` y `support_only=True`, con scope único `batisenal.support`; no ampliar un regex global ni convertir el boundary de lecturas en dispatcher de borradores.

### 2.3 Dependencias

**No se propone ninguna dependencia runtime nueva.** Se confirmó que `requirements-runtime.txt` ya incluye FastAPI, Starlette por su stack, SQLAlchemy, asyncpg, PyJWT (`pyjwt>=2.8.0`), cryptography, Pillow, httpx, python-multipart, bcrypt por passlib y el runtime web. Los caminos `SupportPilot → CaseBridge → transporte ASGI → OAuth` no importan el SDK `mcp`. Conservar las dependencias actuales y registrar las versiones efectivamente resueltas en el venv aislado; no instalar el freeze histórico ni añadir MCP2.3 al servicio.

El SDK MCP opcional de las pruebas existentes tiene su propio pin y runner. Si se utiliza para pruebas de interoperabilidad, se instala únicamente en ese venv de tests y se registra su versión. Un import no previsto o una incompatibilidad que requiera otra dependencia se informa antes de ampliar la spec; no se resuelve instalando paquetes en el venv productivo.

## 3. Identidad real, cliente y procedencia

### 3.1 Vía soportada propuesta

Exponer directamente el recurso SamChat aislado ya diseñado por `mount_pilot`, mediante HTTPS o el mecanismo de túnel MCP soportado que posteriormente se autorice. Usar el OAuth de soporte de SamChat, con **cliente público predefinido** y PKCE S256; no añadir DCR, CIMD ni un IdP nuevo. OpenAI documenta clientes predefinidos y su registro, además de otras opciones. Cliente y callback exactos se toman de la página de gestión del MCP y se contrastan con el registro; no se inventan ni se reutiliza el cliente de Agentius. [OAuth de plugins](https://developers.openai.com/plugins/build/auth).

El cliente de ChatGPT admite conexión de un recurso MCP por HTTPS o túnel y requiere metadata/autenticación compatibles. El alta y el refresco de catálogo se hacen por la UI de gestión soportada; las herramientas de plugin-management disponibles no permiten registrar automáticamente un MCP custom ni un cliente OAuth; puede requerirse una conversación nueva. Esto acredita la existencia de una vía técnica, **no** que ese alta o handshake ya se hayan hecho. No se promete que herramientas nuevas aparezcan automáticamente en esta conversación de Codex. [Conectar con ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt).

No pasar por el proxy genérico de Agentius: su subject `server-owner`, scope `agentius:access` y ausencia de Authorization hacia upstream no son la identidad individual ni el grant de soporte. Sin cambios en `/opt/agentius-hostops`, sus scopes, callbacks, stores o herramientas administrativas.

### 3.2 Consentimiento y autorización

La sesión canónica identifica al empleado local activo, sin suplantación. Debe coincidir con el actor configurado, solicitante del caso y propietario del INFORME. Se releen permisos y denegaciones de lectura de informes/solicitudes, `soporte.self`, namespace/proyecto `request` y facultad de agente exactos. El torneo/proyecto se deriva del documento o cuenta canónicos y debe coincidir con el binding; no se deriva de nombres, rol o texto del caso.

El consentimiento muestra usuario, cliente técnico, caso, ámbito, datos revelables, modo privado y ventana máxima. Confirmación ligada a su huella; state y PKCE S256, resource y callback exactos; código de un uso. Token bearer sólo en el canal autenticado, nunca como argumento JSON. Verificador JWT con claves públicas locales, algoritmo fijado RS256≥2048 o ES256/P-256 y cliente allowlisted; sin fetch de claves elegido por el token.

Cada operación y cada render relee grant, link, empleado, facultades, binding y atestación. El store de identidad usa el mismo owner lógico y un engine de lectura separado configurado como `postgresql_readonly=True`. Un JWT válido no evita revocación. El contexto de bearer es local al request y se limpia al finalizar/cancelar.

La ventana del caso se fija con el primer consentimiento y vence como máximo a los 3600 segundos. Tokens/grants nuevos, refresh, reconsentimiento, reinicio o cambio de perfil no la amplían. No emitir refresh tokens en el piloto. Tras vencer el caso no se abre una ventana nueva modificando su atestación: otra prueba requerirá un nuevo caso/plan expresamente autorizado.

### 3.3 Cambio explícito de procedencia

El constructor, `CaseBridge.authorize`, `current_case_authority` y `CaseService.submit` dejan de fijar `synthetic_stub` incondicionalmente. Aceptan únicamente la procedencia **derivada del binding persistente y del cliente/grant actualmente verificados**. Conservar `synthetic_stub` para pruebas identificadas; admitir `ai_agent` sólo cuando coincidan cliente registrado, issuer/resource, instalación, actor, concesión y atestación sintética.

Una nueva migración aditiva sobre `support_batisenal_v01_case_binding` incorpora `agent_kind`, `agent_client_id`, `agent_issuer`, `agent_resource` y `agent_registration_sha256`. `agent_kind` admite `synthetic_stub`/`ai_agent`, por defecto stub; en modo IA todos los demás campos son obligatorios, no vacíos y el hash tiene 64 hex. El registro público aprobado del cliente se representa por su hash, sin secretos. No backfill a IA. Las filas existentes conservan stub; para otra configuración se provisiona un binding/caso nuevo, sin modificar la atestación.

El trigger de inmutabilidad existente compara todo el registro salvo `revision`, por lo que también protege las columnas nuevas. El rol runtime no puede alterarlas, desactivar triggers o asumir owner. La autoridad coteja las columnas nuevas contra configuración y grant en cada request; la revisión agregada incluye la huella de esa procedencia. `CaseBridge` y el escritor utilizan ese resultado autorizado y persistido; no aceptan `source_kind`, actor, destino o perfil del modelo.

El borrador conserva su procedencia. La huella/versionado del binding se guarda en el registro y metadata PostgreSQL de integración, su auditoría interna y la revisión agregada `snapshot_version`. El envelope y esquema JSON público v1 del recibo se conservan exactamente, sin campos públicos nuevos; la operación ya enlaza al snapshot y su recibo inmutable. No introducir un hash extra en el envelope como cambio encubierto de contrato. La UI distingue «Chatty · conexión configurada» de «Stub sintético · no es Chatty». Esa etiqueta acredita el cliente técnico configurado, no demuestra por sí sola qué modelo produjo una frase. La aceptación real exige además correlacionar una invocación de la conversación de Chatty con transporte, PostgreSQL y render. Nunca renombrar un stub para obtener ese pase.

## 4. Modelo de datos, migraciones y transacciones

### 4.1 Persistencia existente que se reutiliza

Tickets y comentarios canónicos permanecen en sus owners. Los registros nuevos del candidato son metadata de soporte/integración: contexto del caso, eventos/auditoría, borradores de agente, atestación/consentimiento, política, binding, snapshots y operaciones. Los borradores **no** se guardan como `SupportTicketComment`, que es público para solicitante/soporte. No se duplican casos ni se cambian estados financieros.

El operador aislado prepara los prerrequisitos del grafo sintético a partir de los modelos de la revisión actual, mediante un paso owner explícito; nunca desde startup. Después verifica/aplica en orden de dependencia, sólo si ausentes y con hashes revisados:

1. `20260919_agent_action_receipts_v01.sql`.
2. `20261001_private_oauth_storage_review.sql`.
3. `20261023_support_batisenal.sql`.
4. `20261023_support_batisenal_agent.sql`.
5. `20261024_batisenal_pilot.sql`.
6. `20261025_batisenal_v01_policy_fence.sql`.
7. `20261025_batisenal_v01_synthetic_protocol.sql`.
8. `20261025_batisenal_v01_case_storage.sql`.
9. Nueva `20261109_batisenal_real_client_binding.sql` descrita en §3.3, sólo en el esquema sintético.

El protocolo necesita política/binding provisionados por owner en el orden definido por sus FK y funciones; el plan del operador explicita esos pasos y sus hashes, no ejecuta una lista ciega de SQL. Los SQL que ya estén versionados en main se preservan por bytes; sólo se portan los ausentes. Si un script del mismo nombre ya existe en main con bytes distintos al ZIP, prevalece el SQL histórico versionado: no se sobrescribe. Una incompatibilidad se bloquea y cualquier corrección de esquema se presenta como nueva migración aditiva revisable y dentro de la allowlist aprobada, nunca reemplazando SQL histórico o mediante DDL en runtime. Los scripts históricos no son todos idempotentes: un objeto existente sin recibo/hash/esquema compatible bloquea; no se marca como aplicado ni se intenta reparar automáticamente.

La auditoría de transporte conserva su store SQLite explícito y SQL `20261023_batisenal_transport_sqlite.sql`, directorio privado del nuevo entorno. No reutiliza el OAuth de Agentius. Delivery/Events no se instalan ni montan. La decisión de retención/purga productiva queda fuera: no se aplica borrado físico al apagar o revertir y no se inventa conservación indefinida; el manifiesto de prueba identifica el owner y la política aplicable a su evidencia e identidad técnica.

### 4.2 Protocolo y atomicidad

Conservar `SyntheticProtocol` y su exigencia de esquema `bs_test_v01_<32hex>`. Cada transacción de la composición entra en el protocolo antes del primer SQL, con orden de locks compartido por revocación, permisos y cambios relevantes del grafo sintético. No añadir estos hooks a engines de la instalación financiera ni a `public`. Probar con rol efectivo de servicio; un owner capaz de alterar tablas/triggers no es una amenaza resuelta por ese marcador.

La lectura captura una vista coherente, persiste snapshot inmutable y revalida el acceso antes de liberar respuesta. La revisión agrega grafo del caso, epoch de política, versión de regla y procedencia técnica del cliente. Toda fuente está vinculada al snapshot/grant/actor/cliente, no sólo a un ID opaco.

El envío bloquea/revalida, comprueba TTL con reloj PostgreSQL tras esperas y antes de escribir, compara snapshot actual y hash canónico, valida fuentes y persiste **borrador + operación + recibo + auditoría de integración en la misma transacción PostgreSQL**. Unicidad por actor/namespace/torneo/caso/acción/clave; mismo payload recupera el mismo recibo, otro payload da conflicto. El guard posterior a commit vuelve a autorizar la revelación. Un cambio posterior marca stale; no reescribe el recibo ni la propuesta.

No llamar a Chatty ni a otro proveedor dentro de una transacción. Chatty realiza el razonamiento entre lectura y envío; el servidor decide vigencia y autoridad.

La auditoría de transporte SQLite es separada: **no existe atomicidad PostgreSQL–SQLite**. Conservar auditoría de intento y chequeos después de I/O; fallo antes de commit no produce éxito. Fallo de transporte después de commit puede dejar el resultado incierto y debe recuperarse por clave/operación: no afirmar rollback ni crear otra clave automáticamente. Las pruebas ejercen ambos puntos y la revocación durante auditoría/entrega.

## 5. API y transporte

La fábrica ofrece GET `/healthz` sin lectura de datos de caso y GET `/readyz` con resultado genérico del preflight/configuración; readiness no afirma aceptación E2E y el flag apagado no admite operaciones. GET `/panel` redirige localmente a la vista del caso fijo para completar el retorno del login canónico, sin montar el panel financiero. Los handlers nativos de login/logout conservan su contrato.

Conservar el prefijo implementado `/soporte/batisenal/piloto` del recurso aislado. El origen efectivo es un campo requerido del manifiesto; no se fija ahora un dominio, puerto, UID, cliente, callback o llave real.

| Método y ruta | Contrato |
| --- | --- |
| GET `/authorize` | Sesión canónica, parámetros cerrados, preview del consentimiento |
| POST `/consent` | Formulario acotado con Origin/CSRF; confirma la huella exacta |
| POST `/token` | Código único y PKCE; public client predefinido, sin cookies/refresh |
| GET `/case` | Revisión privada del caso configurado y de su vigencia actual |
| POST `/revoke` | Revocación del vínculo/grant del piloto; conserva historia |
| GET `/style.css` | CSS local del piloto |
| POST `/mcp` | Framing moderno y exactamente tres herramientas de caso |
| GET `/.well-known/oauth-authorization-server/soporte/batisenal/piloto` | Metadata del issuer efectivo |
| GET `/.well-known/oauth-protected-resource/soporte/batisenal/piloto/mcp` | Metadata del recurso; challenge 401 apunta aquí |

El issuer es `origin + prefijo` y el resource es `issuer + /mcp`. Callback exacto previamente registrado y admitido por los validadores actuales; sin comodines, URL recibida del prompt o redirecciones abiertas. Metadata declara authorization_code, S256 y token endpoint auth `none`; no anuncia DCR ni refresh. Omitir/false `authorization_response_iss_parameter_supported` mientras no exista `iss` real en la respuesta de autorización. No anunciar endpoints/capacidades inexistentes.

Las tres herramientas conservan nombres y contrato JSON v1 del candidato:

| Herramienta | Entrada cerrada / efecto |
| --- | --- |
| `samchat_batisenal_get_case_context` | `schema_version="1"`, `case_ref`; snapshot inmutable y fuentes del único caso |
| `samchat_batisenal_submit_case_draft` | caso, snapshot/versión esperada, clave y propuesta declarativa; únicamente borrador privado |
| `samchat_batisenal_get_case_operation` | caso y exactamente una de `operation_id`/`idempotency_key`; recupera recibo original con vigencia actual |

Sin actor/empresa/perfil/source_kind/credencial/destino/visibilidad en argumentos; claves extra, JSON duplicado o Unicode inválido se rechazan. No herramientas de listado general, comentarios, publicación, adjuntos, archivos, shell, SQL, aprobación o pago. Los IDs son selectores y no capacidades bearer.

Conservar límites v0.1: respuesta de contexto≤32KiB UTF-8; tres fuentes cerradas (`expense_report`, `user_context`, `procedure`) dentro del máximo de12, extracto≤2000 caracteres; `complete_for_scope=False` y omisiones explícitas. No adjuntos ni detalles fiscales. Solicitud de borrador≤16KiB, summary/textos≤2000 caracteres,≤20 claims y≤8 referencias por claim; hechos requieren fuentes del snapshot. Importes como strings decimales y UTC/RFC3339. El hash canónico conserva orden de arrays y Unicode sin normalizar; claves ASCII ordenadas y sin espacios, no floats ni hashes aportados por el cliente.

Mantener las comprobaciones del transporte moderno `2026-07-28`, framing/metadata y límites de red del candidato. La versión negociada por el cliente real se registra en el handshake. Si no es compatible, se declara bloqueo de interoperabilidad y se propone el delta antes de ampliar el contrato; no eliminar validaciones para obtener un pase.

Errores: autenticación antes de semántica de objetos; objetos ajenos/inexistentes→`RESOURCE_UNAVAILABLE` sin confirmar existencia; stale/conflicto sólo tras autorizar. Errores acotados sin stack, secretos, cuerpo del caso o URLs privadas. Lecturas sensibles no-store y nosniff; referrer de formularios según parche moderno. Sin CORS abierto ni relajación de Origin para adaptar un proxy.

## 6. UI y soporte heredado

Reutilizar el renderer de `batisenal_pilot_routes`, el panel y las utilidades del widget. La instalación aislada presenta un acceso Batiseñal al **caso preconfigurado**; no importa ni monta todos los routers financieros para obtener navegación. Sirve sólo el asset local necesario, no un directorio de artefactos ni recursos externos.

El modo UI del piloto no ofrece creación de otros casos, captura/adjuntos, comentario público ni publicación de borradores. El servidor también los deniega; esconder controles no es autorización. El caso ya fue provisionado por owner. El panel permite abrir/minimizar/reabrir conservando texto y referencia en memoria, con revalidación al volver; no persiste texto sensible en almacenamiento del navegador. La adaptación del widget limita sus endpoints al caso configurado y a lectura/revisión privada. No montar sin filtro el router general antiguo con POST de creación/respuesta.

Estados distinguibles: pendiente de conexión/consentimiento; consulta real en curso; resultado por confirmar; borrador privado guardado; contexto cambiado; acceso terminado. No mostrar IA trabajando por haber consentido, ni aprobación/pago/resolución por guardar un borrador. Tras timeout se conserva la clave y se ofrece recuperación; cancelar la espera no afirma cancelar una transacción.

Aplicar las exclusiones del marcador reservado `batisenal:private` y la atestación en **todos** los caminos heredados relevantes del owner de soporte: listas propias/admin, contadores, detalle, creación con marcador reservado, comentarios y actualizaciones. No permitir bypass por admin/superadmin. Los borradores privados permanecen en su owner, no en comentarios públicos. Verificar por HTTP accesos directos y por invocación del servicio. Conservar el comportamiento ordinario de tickets no Batiseñal.

El piloto usa el mismo propietario para consentir y revisar; no añade otro revisor. La atestación bloquea `reply_case` y `publish_draft` incluso tras revocación, expiración, apagado o desmontaje. Mantener ese bloqueo al revertir código; nunca quitar la atestación para publicar.

## 7. Background jobs

Ninguno. No Events, suscripciones, callbacks de entrega, outbox, polling de servidor, notificaciones o tareas de razonamiento en segundo plano. La invocación de Chatty es explícita. El polling de panel, si se conserva, mantiene límites/backoff y sólo funciona abierto/visible; cerrar cancela lecturas y no invalida recibos ya comprometidos.

## 8. Operador, manifiesto y activación posterior

Implementar un operador limitado con `inventory`, `plan`, `apply` y `verify`; `plan` es el valor por defecto y no escribe. Selectores explícitos: revisión/árbol, target aislado, instalación, esquema, role owner/runtime, actor, torneo, caso e INFORME. `apply` exige plan/hash exactos, actor administrativo autorizado, confirmación y respaldo/snapshot de recuperación; rechaza destino productivo o ambiguo. No auto-DDL, auto-perfiles, auto-grants o credenciales en startup.

El manifiesto cerrado, no secreto, contiene release/árbol, instalación y owner; identidad del destino PostgreSQL y esquema; roles efectivos; hashes de migraciones/política/atestación; actor/caso/informe/torneo/namespace/proyecto; URLs públicas exactas; cliente/callback y hash de registro; referencias privadas de signer/verificador y huellas públicas; cookie/origen propios de la instalación aislada; paths privados de auditoría y evidencia; referencias de retención; protocolo; flag y versión de aprobación. Los secretos se inyectan por custodia existente, no se incluyen en el manifiesto, logs, PR o argumentos del modelo.

La validación local de formato, selectores y allowlist del manifiesto ocurre antes de abrir conexiones y rechaza un DSN de producción conocido o no aprobado. No intentar consultas de prueba sobre la base financiera. Sólo contra el destino ya autorizado como aislado, un preflight de metadata de PostgreSQL comprueba base, rol, search_path, aislamiento y privilegios antes de consultas del dominio; esas comprobaciones requieren SQL de catálogo y no se promete hacerlas sin SQL. Después se crean los engines de dominio y se instala su protocolo antes del primer SQL de negocio. El engine de identidad permanece de lectura y no recibe un hook que intente locks de escritura. La infraestructura de prueba y sus prerrequisitos se provisionan de manera explícita por su owner. El operador no copia registros de producción. Los únicos cambios de identidad durante la ejecución proceden del login/sesión/auditoría canónicos y del consentimiento/grant previstos, no de privilegios concedidos por el modelo. Las comparaciones de cero efectos financieros cubren documentos, gastos, pagos y presupuesto; no confunden esos registros con los recibos técnicos que el piloto sí debe crear. La cuenta legítima del piloto se enrola por el mecanismo canónico, sin crear un bypass de login para la demostración. El permiso para construir un operador no autoriza ejecutarlo contra un destino real.

| Dato/efecto pendiente | Estado y condición |
| --- | --- |
| Dominio/recurso accesible, proxy/túnel, puerto, UID/GID y servicio de prueba | No elegidos ni instalados; requieren plan operativo y aprobación del destino |
| Cliente predefinido/callback exactos, alta MCP y catálogo en Chatty | No registrados por este trabajo; leer registro real, aprobar/instalar y verificar discovery/handshake |
| Cuenta piloto local y facultades/binding | No concedidos; owner autoriza selectores y usuario realiza login/consentimiento |
| Custodia de signer, cookie aislada y stores | Referencias/huellas por confirmar; nunca generar o copiar secretos para llenar campos |
| Esquema/roles/fixture/SQL y política de evidencia | Plan/hash/backup y prueba de mínimos antes de aplicar en la instalación de prueba |
| Navegador y conversación reales | QA humana/cliente auténticos; los stubs o overrides no cumplen A1/A2 |

Estos pendientes bloquean la **instanciación accesible y aceptación E2E**, no la construcción apagada aprobada. No se presenta el checkpoint de identidad como cerrado sólo porque exista código o documentación oficial. Si la vía soportada no puede materializarse, el cierre se limita al código/evidencia disponible y se identifica el bloqueo concreto.

## 9. Archivos previstos y propiedad de fases

Allowlist propuesta; ningún otro dominio financiero/tournament se modifica para el piloto. Todo archivo portado procede de la copia verificada del candidato moderno y se reconcilia con main; no se reemplazan carpetas enteras.

| Grupo | Archivos permitidos / razón |
| --- | --- |
| Host/operación nuevos | `src/samchat/batisenal_isolated_app.py`, `src/samchat/batisenal_composition.py`, `scripts/batisenal_real_pilot.py`, `config/batisenal-pilot-manifest.schema.json`: fábrica, inyección, manifiesto y operador apagados |
| Núcleo del candidato | `src/samchat/batisenal_bridge.py`, `batisenal_case_bridge.py`, `batisenal_case_contract.py`, `batisenal_pilot.py`, `batisenal_policy_fence.py`, `batisenal_synthetic_protocol.py` |
| Transporte moderno mínimo | `src/samchat/batisenal_transport/{__init__,contracts,framing,http,store}.py`; se excluyen Events/delivery/callback |
| Owners del candidato | `src/devnous/gastos/services/batisenal_{service,agent_service,pilot_service,case_authority,case_service}.py` |
| UI | `src/devnous/gastos/routes/batisenal_routes.py`, `batisenal_pilot_routes.py`, `static/batisenal.js`: modo aislado de caso fijo, consentimiento/render y minimizado |
| Extensión OAuth acotada | `src/samchat/private_plugin/oauth_flow.py`, `oauth_store.py`; mantener authorization/contracts/identity/adapters y scopes de lectura actuales salvo incompatibilidad demostrada que requiera revisión de alcance |
| Legado | `src/devnous/gastos/services/support_ticket_service.py`, `src/devnous/gastos/routes/support_routes.py`: exclusión del marcador, denegación de acceso directo y regresión de tickets ordinarios |
| SQL | Las ocho migraciones de §4, `20261023_batisenal_transport_sqlite.sql`, y nueva `database/migrations/20261109_batisenal_real_client_binding.sql`; portado revisado, aplicación sólo por operador |
| Tests | `tests/unit/batisenal/`, `tests/integration/batisenal/`, `tests/browser/test_batisenal_real_pilot.py`, `tests/browser/fixtures/batisenal/`, `scripts/batisenal/run_isolated_acceptance.py`; sólo suites/fixtures/harness del alcance descrito |
| CI acotado nuevo | `.github/workflows/batisenal-real-pilot.yml`: check `Isolated Batiseñal pilot checks (not Chatty E2E)`, sin modificar workflows globales ni protecciones |
| Documentación | `docs/integrations/batisenal/real-pilot.md`, `docs/roadmap/batisenal-real-pilot-20261109-{story,spec}.md`, evidencia del cierre y manifiesto de portado/hash |
| Canon, sólo tras revisión expresa | Los tres canons y `docs/roadmap/samchat-convergence-register.md`, exclusivamente las adiciones literales de §12 y sus hashes |

No se prevén cambios a `requirements-runtime.txt`, gateway Agentius, `copa_telmex_dashboard.py`, `user_routes.py`, modelos financieros, migrations de negocio existentes, configuración del servicio financiero ni CI global. Si el import graph o la aceptación demuestra un archivo adicional imprescindible, se justifica su delta antes de ampliar la allowlist. El checkout principal tiene cambios ajenos; construir en worktree nuevo de la base aprobada y no revertirlos.

## 10. Plan de pruebas y aceptación

Probar el **código integrado**, con imports de `src`, no únicamente el ZIP. Mantener separadas tres evidencias: regresión sintética #516, aceptación técnica del código integrado y recorrido real de Chatty. Cada evidencia identifica código/árbol, esquema/migraciones, roles, config/manifest hash, versiones efectivas, requests/operaciones saneados y resultado observado.

| Criterio | Verificación mínima |
| --- | --- |
| A1 | Invocación real desde Chatty, lectura→propuesta→commit→render privado en navegador; correlación de cliente/request/receipt y fingerprint del release; ninguna sustitución por stub |
| A2 | Login real y consentimiento nativo; usuario/client/grant derivados de servidor; argumentos de identidad rechazados; stub visible y nunca aceptado como IA; otro cliente/callback/issuer/grant denegado |
| A3 | Segundo actor/caso/empresa/torneo/grant/source/draft denegados en herramienta, operación, UI y todas las rutas/owners heredados; también admin/superadmin |
| A4 | Antes/en/después del TTL con reloj PostgreSQL; refresh/reconsentimiento/reinicio no extienden ventana; revocación y pérdida de permiso en lectura/envío/estado/render |
| A5 | Dos conexiones/workers con barreras reales: ambos órdenes de revocación y autorización; tras espera en locks/I/O se relee vigencia; diferenciar contenido ya liberado |
| A6 | Cambiar informe/partida/fuente/regla/política/procedencia en el grafo sintético antes/después del envío; STALE_CONTEXT o marca stale según orden; replay de otro grant/caso denegado |
| A7 | Concurrencia real y UNIQUE: una operación/borrador/recibo para misma clave+payload; conflicto si cambia; claves JSON reordenadas iguales, arrays/Unicode alterados distintos |
| A8 | Fallo antes/tras commit, pérdida de respuesta, fallo de auditoría PG y transporte SQLite; recuperación conserva recibo/clave, rollback no anuncia éxito y commit incierto no duplica |
| A9 | Fuentes/versiones ajenas falsas rechazadas, 3fuentes exactas, omisiones y límites; prompt injection no amplía capacidades ni dispara llamadas, hipótesis explícitas |
| A10 | Errores indistinguibles para ajeno/inexistente; no secretos/PII innecesaria en logs/caché/render; sin recursos remotos; publicación humana bloqueada incluso tras apagado |
| A11 | Capturas de navegador real desktop/mobile, teclado/foco/labels, minimizar/reabrir conserva estado y revalida, espera/error/timeout/stale/expiración; no DOM sólo ni capturas de otra revisión |
| A12 | Flag apagado y desmontaje detienen operaciones/revelación; vuelta al código compatible con filtros privados; invariantes antes/después de tablas de negocio; atestación e historia intactas |

Regresión adicional: OAuth normal sigue rechazando soporte; tickets ordinarios conservan comportamiento; nuevos motores aislados no cambian setters de otro proceso; errores locales de manifiesto/DSN impiden conexión, y discrepancias de identidad DB/schema/rol detectadas por SQL de metadata del destino aislado impiden consultas del dominio; ninguna tabla de integración se crea por startup; SQL rechaza `public`; `search_path` no resuelve tablas ajenas; rol runtime no asume owner ni tiene privilegios efectivos de DDL o escritura financiera.

Las pruebas de carreras ejercen política y PostgreSQL reales, conservando orden de transacciones/locks y no sólo un contador verde. Instalar el grafo sintético y auditar rollback en un clúster desechable Unix-only por usuario no root. No usar DSN externo/productivo para tests. El harness rechaza configuración de negocio real.

Comandos previstos sobre el venv de pruebas aislado: runners actuales `scripts/private_plugin/run_offline_tests.py` y `run_postgres_tests.py`; nuevas suites path-scoped unit/integration/browser del piloto; bash/AST cuando corresponda, Black/isort/flake8/mypy de módulos cambiados y cobertura individual≥85% en módulos nuevos/cambiados. La matriz identifica bloqueos e infraestructura/preexistencias; no oculta skips de criterios obligatorios. El nuevo workflow path-scoped `.github/workflows/batisenal-real-pilot.yml` reutiliza los patrones existentes de Ubuntu24.04, Python3.12, instalador CPU y PostgreSQL efímero no root. Usa permisos mínimos `contents:read`, no secrets ni DSN externos, y ejecuta `python scripts/batisenal/run_isolated_acceptance.py --tier technical` más las comprobaciones de cobertura y navegador del código integrado. El check se llama `Isolated Batiseñal pilot checks (not Chatty E2E)`: verifica contratos, persistencia, aislamiento y UI con datos/identidades de prueba correctamente rotulados; no invoca ni sustituye a Chatty real. Conserva evidencia JUnit/cobertura/QA de esa revisión. Todos los checks requeridos del PR y este gate propio deben concluir antes del checkpoint final; A1/A2 reales continúan como aceptación posterior separada. No se altera CI global para reparar fallos ajenos sin revisión del alcance.

## 11. Fases, riesgos y reversión

1. **Conciliación y portado apagado:** manifiesto de fuentes/hashes, aislamiento de imports/proceso y regresiones OAuth/legado. Ninguna ruta se añade al servicio financiero.
2. **Persistencia y procedencia:** operador dry-run, migraciones/constraints/roles en PG desechable, fuente técnica verificada y atomicidad/races. Tests stubs permanecen identificados.
3. **UI y contrato integrado:** rutas fijas, modo privado/minimizado, metadata honesta y transporte moderno; QA de construcción separada de A1/A2.
4. **Revisión independiente y diff:** cobertura/matriz, fallos clasificados, alcance/canon y reporte antes de PR/merge. No declarar listo el recorrido real si sólo pasa código apagado.
5. **Plan operativo posterior:** resolver y presentar manifiesto concreto de §8, efectos de instalación/alta MCP y reversión para autorización; luego login/consentimiento auténticos y matriz real. Producción general y publicación humana siguen fuera.

Riesgos principales: exposición por soporte legado; scopes de lectura ampliados; procedencia IA falsa; sesión/DSN accidentalmente productivos; privilegios heredados o owner en runtime; constructor que ejecute SQL antes del protocolo; error de transporte confundido con rollback; dependencia de catálogo/cliente no materializada; degradación del sandbox de navegador para obtener QA. Cada riesgo tiene un gate explícito en §§2–10; si falla, no activar.

Reversión del código aislado: apagar flag, bloquear nuevas operaciones y revelaciones, revocar únicamente los vínculos/grants del piloto por su owner y retirar la nueva composición/rutas del entorno de prueba autorizado. Conservar stores, atestaciones, caso, drafts, operaciones, recibos y auditoría. No DROP, purga ni quitar filtros/atestación. No volver a una versión que publique o enumere el marcador privado; el rollback debe mantener sus guards. La migración aditiva se conserva al revertir aplicación; no se borra evidencia para restaurar un esquema anterior.

## 12. Propuesta literal de canon para revisión humana

**No editar los canons con esta redacción hasta que la persona revise y apruebe expresamente el texto.** La spec propone estas adiciones; la automatización no las aprueba. No se afirma que la integración esté implementada, desplegada o aceptada.

Producto, adición propuesta fechada2026-11-09:

> Diseño de piloto aislado Batiseñal con Chatty real: una persona realmente autenticada puede consentir lectura acotada de un único caso e INFORME sintéticos y la creación de una propuesta privada. SamChat conserva autoridad de identidad, ámbito, vigencia, fuentes, idempotencia y recibo; Chatty no recibe facultad de aprobar, pagar, modificar el informe ni publicar respuestas. La atestación del piloto impide publicación incluso humana. La integración sólo se considera aceptada con evidencia correlacionada de cliente real, PostgreSQL y navegador de la misma revisión. Código apagado y pruebas sintéticas son evidencias separadas de conexión y aceptación reales. Producción general y publicación son decisiones posteriores.

Ingeniería, adición propuesta fechada2026-11-09:

> La composición del piloto Batiseñal utiliza un entrypoint aislado con los owners canónicos de autenticación y soporte, sin importar el startup financiero ni reutilizar credenciales/cookies productivas. El OAuth de soporte se mantiene separado del contrato de lecturas y del scope administrativo de Agentius. Caso y grafo sintéticos usan esquema y protocolo transaccional propios; ningún hook se aplica a la instalación financiera. Borrador, operación, recibo y auditoría de integración se comprometen atómicamente en PostgreSQL. La auditoría de transporte separada no constituye una transacción distribuida. La procedencia técnica deriva de cliente/grant/binding verificados y nunca de un argumento del modelo ni de renombrar un stub. Cliente, destino, permisos e instalación efectivos requieren manifiesto y autorización específicos antes de activar.

Sweep, adición propuesta fechada2026-11-09:

> El PR516 incorporó CI y fixtures de composición sintética, sin montar Batiseñal en el runtime financiero. Se aprobó la historia de una primera entrega real aislada; su especificación propone portado controlado, procedencia auténtica, esquema sintético y retorno privado. Implementación, alta de cliente/herramientas, instalación y aceptación real conservan evidencia y checkpoints propios. No hay activación productiva ni aceptación E2E acreditadas por este documento.

Razón/evidencia a referenciar: historia aprobada, esta spec y el futuro reporte del cambio concreto. Si se aprueban estas adiciones con la spec, el mismo PR actualizará únicamente esas secciones y los tres hashes del registro, con fecha y evidencia. Hasta entonces `Canon unchanged` en la planificación: no se han alterado producto ni autoridad en runtime.

## 13. Checklist para revisión de esta propuesta

- Historia aprobada conserva su alcance: piloto aislado, usuario real autenticado, datos sintéticos, tres herramientas y borrador privado.
- Arquitectura de host aislado y delta de procedencia expuestos para aprobación; cero dependencias runtime nuevas propuestas.
- Datos de destino/cliente/llaves/permisos no inventados; su ausencia bloquea instalación accesible y A1/A2, no se disfraza como pase.
- Aplicación de SQL, alta MCP, consentimiento, red, servicios, PR/merge y activación siguen fuera de esta aprobación de construcción.
- Publicación humana permanece bloqueada y el servicio financiero/gateway administrativo se conservan fuera del cambio.

Esta especificación no ejecutó pruebas ni cambió aplicación, Git, servicios, bases, permisos o credenciales. Sólo se creó este documento de planificación para revisión.
