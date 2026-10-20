# SamChat readonly: preparación fmfg, revisión 2026-10-11

Estado: candidato aislado publicado en PR #476; sin despliegue ni activación productiva. Sustituye
las afirmaciones de estado del documento de 2026-10-08; el paquete anterior se conserva.
Base del worktree: 2cb5397769fa1f7532ec495a68a19cbc4a6778c7.
Fuente activa inspeccionada: 514b6542e90c1eaae5c26351bc9ee18e9d0f86e8.
La actualización DIOT cambió user_routes.py, no las rutas de informes ni los
helpers de visibilidad/autorización. Identidad, ACL y tipos se revalidaron el11.

## Historia y alcance vigente

Por instrucción expresa del usuario: fmfg@pm.me consulta únicamente INFORME
que su empleado ya puede leer en Plataforma Sports, con permisos vigentes en
cada consulta. El usuario confirmó que su conector Agentius es exclusivo.
Actor resuelto por coincidencia única de correo en empleados:
b8816679-ad77-4590-83d5-50ffce335854; activo, superadmin, Dirección.
La sesión canónica usa empleado_id = empleados.id. No se pidió su contraseña.

Autorización vigente: correcciones, commit, push, PR, merge y activación conforme
al recibo exacto y sus puertas obligatorias. La aprobación no prueba ejecución:
roles, credenciales, instalación, reinicios y canary productivo siguen pendientes.
No se autoriza ampliar alcance, permisos ni eludir una denegación.

## Especificación del candidato

La lista fija de 1–1000 documentos y la intersección de torneos no implementan
ese alcance dinámico. Se conserva ese camino anterior para otros candidatos;
la nueva política live_informes_v1 es exclusiva del actor verificado y no acepta
documentos, torneos, identidad, SQL, credenciales ni paths como concesiones.

Diez vistas security_barrier de samchat_ro_fmfg_v1 evalúan el empleado activo,
correo/rol/departamento y reglas ver vigentes en cada SELECT. Solo INFORME;
propietario o roles canónicos, con restricción departamental admin/coordinador.
Se usa session_user exclusivo, no SET ROLE ni variables controlables por cliente.
Denegaciones explícitas prevalecen aun para superadmin: es una restricción
adicional del candidato, no una modificación del bypass canónico.
Cambios/revocación se comprueban antes/después del SELECT y otra vez tras audit.
Revalidación final conserva una copia de los hechos, no un objeto mutable.

Login propuesto samchat_ro_fmfg, owner NOLOGIN samchat_ro_fmfg_owner, sin
membresías, flags privilegiados, base-column access, TEMP/CREATE o DML.
El owner recibe solo columnas concretas de las diez tablas; el login solo vistas.
PUBLIC TEMP se revocaría preservándolo para devnous_user y postgres: cambio
compartido explícito que requiere aprobación y preflight de drift de ACL/roles.
El verifier rechaza funciones SECURITY DEFINER ejecutables no sistémicas.

Se registran únicamente cinco herramientas: resolve_reference, document_status,
document_diagnose, document_audit, study. Estudio solo documents. Presupuesto,
lectura de fuentes y mutaciones no se publican en este lane. El owner presupuestal
inyectable y sus pruebas anteriores se conservan, pero no se habilitan versiones.
Diagnóstico no representa elegibilidad completa ni permiso para aprobar; deja
first_workflow_blocker y approval_eligibility unknown, sin inventar gates/no-self.

Puente existente UID993 → socket local del nuevo worker/gateway UID988 GID983
→ login dedicado PostgreSQL → audit fsync fuera de negocio.
SO_PEERCRED en ambos extremos; principal/hash/delegación resueltos por servidor.
El hop TCP previo exige el UID994 de openai-tunnel en el tuple establecido
exacto de /proc/net/tcp: si falta, hay error o UID distinto, no sirve samchat.*.
No usa headers de identidad ni aplica esta restricción a las catorce herramientas
previas. Root y el propio túnel permanecen en el perímetro de confianza del host.
La exclusividad del túnel es declaración del usuario, no identidad de sesión
SamChat autenticada por el túnel. Si se comparte, se revoca esta delegación:
no se permite servir varios usuarios con este mismo actor.
Se preservan las catorce herramientas y el worker existentes; no se modifica
el archivo activo del puente. Patch opt-in y release separado propuestos.

Credencial nueva de máquina via LoadCredential, nunca password del empleado.
service.py exige archivo de activación root:983 0640 con autorización explícita
y hash exacto. Valores propuestos autorizados=false por defecto.
provision_service_access.py es un artefacto owner-run, no se ejecutó.
--verify-metadata prepara verificación efectiva con SELECT 1, actor y catálogos,
sin documentos. --prune funciona después de la expiración de la delegación.

Audit propuesto /var/lib/agentius-samchat/readonly-audit/fmfg, UID988/GID983,
directorio0700, JSONL0600, una cadena diaria fsync, sin inputs/respuestas/secretos.
Retención 30 días calendario UTC (día corriente más29); timer diario00:05UTC.
Archivos corruptos o permisos incorrectos provocan error; no se borran para
ocultar evidencia. El fallo de audit impide entrega de resultados.
Expiración doble DB/delegación: 2026-11-08T23:59:59Z, sin renovación automática.

## Antecedentes de aceptación (anteriores al cierre de las tres correcciones)

En PostgreSQL16 instalado, clúster efímero de socket /tmp/samchat-ro-native-20261011-ElURXtHr,
sin listener TCP, tablas/filas sintéticas y roles separados. Driver instalado
Python3.12.3 / asyncpg0.30.0. Tipos de fixture obtenidos de catálogos activos.
No se consultaron documentos de negocio durante preparación.
La inspección de identidad leyó solo campos no secretos del empleado indicado;
la conectividad usó el acceso de servicio preexistente, sin exportar credenciales.

```sh
SAMCHAT_TEST_SOCKET=/tmp/samchat-ro-native-20261011-ElURXtHr PYTHONPATH=src /srv/samchat/venvs/release-598e7a286/bin/python scripts/readonly_gateway/run_offline_tests.py --native-informes
SAMCHAT_TEST_SOCKET=/tmp/samchat-ro-native-20261011-ElURXtHr PYTHONPATH=src /srv/samchat/venvs/release-598e7a286/bin/python scripts/readonly_gateway/check_quality.py --native-informes
flake8 --max-line-length=88 --extend-ignore=E203,W503,E501 src/samchat/readonly_gateway tests/unit/readonly_gateway scripts/readonly_gateway
python3 -m compileall -q src/samchat/readonly_gateway tests/unit/readonly_gateway scripts/readonly_gateway
systemd-analyze verify deployment/readonly_gateway/samchat-readonly-fmfg.service deployment/readonly_gateway/samchat-readonly-fmfg-prune.service deployment/readonly_gateway/samchat-readonly-fmfg-prune.timer
patch --dry-run /opt/agentius-samchat/0.2.1-persistent-8097689-bundle1/bridge/executor.py deployment/readonly_gateway/agentius-executor-catalog.patch
git diff --check
```

Pruebas cubren scope dinámico/nuevos informes, aislamiento de otro login,
SQL literal/identidad falsa, denegación y baja, cambios de rol/departamento,
revocación en audit, puente AF_UNIX+auditoría durable, historial/diagnóstico/
paginación/estudio, timeout real3s y cancelación en vuelo, retención/tamper,
composición protegida, presupuesto canónico inyectado y nueve casos legacy.
Los nueve casos pendientes se ejecutaron sin modificar sus fixtures/expectativas
en una segunda base efímera nativa, no instalando PGlite.
Errores corregidos del candidato: copia del proof de revocación, ISO dates para
asyncpg, timezone consistente del fingerprint, cancelación thread-safe.
Teardown corregido de fixture temporal: DROP DATABASE separado de DROP ROLE.

Black/isort ejecutados y correctos usando módulos del baseline instalado.
Mypy aislado del módulo (Python3.12, imports ajenos skip) pasa20 archivos; no
sustituye el chequeo del proyecto. El chequeo con pyproject conserva84 errores
preexistentes en6 archivos ajenos, además de su targetPython3.8 incompatible.
Revisión de cierre del11: Bandit1.9.4 y stevedore5.9.0 se ejecutaron directamente
desde wheels ya existentes en caché, por zipimport, sin instalación ni red.
El slice no tiene hallazgos HIGH/HIGH; el repositorio reproduce exactamente sus
seis hallazgos HIGH/HIGH admitidos por baseline. El escaneo ampliado del slice
reporta13 LOW y16 MEDIUM: SQL construido con identificadores cerrados/validados
y valores ligados, subprocess owner-run con comandos constantes, y socket0660
limitado por UID del peer. No se silenciaron checks ni modificó el baseline.
Se corrigió una carrera AF_UNIX: cierre con bytes de petición sin leer descartaba
el mensaje AUTH_REQUIRED y producía SOURCE_UNAVAILABLE. Se drena un único frame
acotado con timeout1s antes del rechazo, sin resolver principal ni llamar gateway.
La regresión comprueba100 rechazos por UID y JSON inválido sin dispatch.
Se controla además BrokenPipe al responder a peers desconectados: un rechazo
no termina el accept loop, y no se resuelve principal ni se llama gateway.
Antes del wiring CI pasaron72 tests por unittest y pytest; cobertura real coverage.py87,65%,
1434/1636 statements. Trace histórico no se presenta como cobertura CI.
Mypy del slice con PYTHONPATH=src y --no-incremental pasa20 archivos; la comprobación
explícita de anchors legacy reproduce84 errores en6 archivos iguales al commit
base, y persiste el targetPython3.8 incompatible. No equivale a mypy de todo src.
En ese momento la CI integral seguía sin ejecutar. Se reprodujo y corrigió un defecto del candidato:
sin SAMCHAT_TEST_SOCKET, nueve casos legacy fallan en setup porque Engine busca
PGlite en una ruta Windows fija. El workflow anterior no preparaba socket ni PGlite;
además omitía los casos live por skipUnless sin ese socket. La corrección integra
un runner PostgreSQL16 sintético de socket efímero en unit-tests, excluye el slice
de la pasada genérica sin motor y combina su coverage con la del repositorio,
igual que el patrón private-plugin existente.
El usuario autorizó el cambio acotado al workflow compartido. Se agregó ese
runner de socket, exclusión de la pasada genérica y combinación de las tres
fuentes de coverage. Preflight exige herramientas PostgreSQL16 presentes;
no usa el servicio PostgreSQL del host ni lo instala. EXIT trap detiene solo
el clúster creado incluso ante fallo de tests. El bloque YAML exacto pasó
como usuario no-root en checkout sintético:73 tests, cero omisiones,
coverage.py87,65% y clúster detenido al salir. Incluye una regresión del
wiring/safety/coverage. El contrato check-pr-quality-gate pasa sin cambios
en baselines ni relajación de puertas. No equivale a CI remota
completa: no hubo push/PR ni se dispensó deuda heredada de tipos.
No hubo instalación. Pruebas/gates parciales no son aceptación productiva.
No hay canary, rol instalado, audit mount activo ni E2E del túnel productivo.

## Correcciones acotadas aprobadas y revisión de fuentes

El usuario aprobó regenerar/revisar inventario y matriz, hacer portátil la fixture
del puente y resolver únicamente los dos nuevos diagnósticos de tipos. El estado
de ejecución vigente está en VALIDACION-INTEGRAL-FMFG.json y en el recibo único;
los párrafos anteriores conservan la cronología, no afirman el estado actual.

El mínimo soportado declarado es Python3.12: `requires-python = ">=3.12"`,
AGENTS exige3.12+ y el canon de CI exige3.12 por PEP701. Se alinea exclusivamente
`tool.mypy.python_version` con ese mínimo; no se cambia compatibilidad runtime,
no se corrigen los84/3358 errores heredados ni se declara mypy global verde.

El inventario AST pasa419→439 archivos, sin ampliar sus569 rutas,44 acciones,
36 perfiles ni13 registros; cero parse gaps. Solo cambia source_file_count en
route-inventory.json. La matriz se regeneró y permanece idéntica byte por byte
(SHA256763c2e654a7f0dc3b383fd96dd1c5a36d439bad4f165b08313236aa7e0e48a55).
Las veinte fuentes nuevas, todas en src/samchat/readonly_gateway, se explican así:

| Fuente | Responsabilidad; no es concesión de acceso |
| --- | --- |
| __init__.py | Identificación del paquete aislado |
| agentius.py | Decorador opt-in del catálogo y despacho tipado |
| asyncpg_driver.py | Conexión dedicada, timeout, rollback y cancelación |
| audit.py | Auditoría técnica durable; no escritura de negocio |
| canonical_policy.py | Equivalencia del predicado canónico de rutas |
| catalog.py | Catálogo reutilizable; el lane seleccionado lo restringe |
| contracts.py | Contratos y errores neutros |
| daily_audit.py | Cadena diaria, permisos y retención30d |
| delegation.py | Binding cerrado, integridad y vigencia |
| diagnosis.py | Diagnóstico sin autoridad de aprobar |
| fixtures.py | Datos exclusivamente sintéticos para ensayo |
| gateway.py | Validación de entradas, despacho y límites |
| live_informes.py | Visibilidad dinámica INFORME y revalidación de permisos |
| mcp.py | Framing de transporte; no concede identidad |
| owners.py | Owners inyectables; presupuesto no habilitado para fmfg |
| postgres.py | SELECT acotados, verificación de rol y esquema |
| repository.py | Repositorio sintético/local, no fuente productiva nueva |
| service.py | Composición explícita con activación default false |
| trusted_tunnel.py | UID exacto de cliente TCP mediante kernel |
| unix_bridge.py | Socket Unix, SO_PEERCRED y proxy de cinco herramientas |

ReportOnlyGateway y UnixReportProxy solo permiten resolve_reference,
document_status, document_diagnose, document_audit y study(documents).
Presupuesto, source_info/source_read y mutaciones quedan excluidos. SQL/vistas,
delegación, actor, vigencia, UID/GID y auditoría no cambian en esta corrección.
Las14 herramientas heredadas del puente se preservan: esta restricción pertenece
a las herramientas SamChat añadidas, no convierte todo Agentius en solo lectura.

La fixture portable conserva executor.py, work_tools.py e ish-manifest.json como
archivos .fixture no ejecutables, con los hashes de las fuentes admitidas.
Se verifican todos antes de copiar/importar; falta, symlink o tamper hacen fallar
la prueba. HTTPServer, Executor y WorkClient se mockean; no hay acceso al worker,
servidor HTTP real ni documentos ISH. El manifest contiene únicamente paths,
commit, tamaños y hashes no secretos. Ningún dato de negocio se empaqueta.
Se conservan comprobaciones14+5 herramientas y study exclusivo documents.

La reproducción local en namespaces no equivale a una ejecución de GitHub.
Publicación verificada: commit bd7d43edacb81ae10a7509f93531ea7c63fd9323,
rama feat/samchat-readonly-fmfg-20261011 y PR #476. Sin instalación,
roles nuevos ni activación productiva.

## Entrega y canon

Recibo único y manifest de hashes en /root/readonly-delivery-20261009.
El recibo reúne permisos/columnas/actor/transport/UIDs/rutas/vigencia, datos
consultados y propuestos, cambios, pruebas, comandos y rollback por fases.
La instalación solo podrá ejecutarse después de aprobación de ese objeto exacto,
con revisión de drift y verificación metadata-only previa al montaje del puente.
CI/herramientas pendientes requieren entorno autorizado antes de promoción.

Canon unchanged: ninguna capacidad/autoridad de negocio o runtime activa cambia.
No se tocaron otros candidatos. La publicación del candidato no activa producción.

## Continuación de PR #476

Main integrado: 974f560766520f5a2370ade5c799d00c1f4f708d. Canons humanos
verificados contra su registro; sin ediciones. El inventario combinado añade
únicamente veinte fuentes readonly a main (423→443), manteniendo sus rutas,
acciones, perfiles y matriz. Los resultados anteriores conservan su baseline
histórico y no sustituyen las validaciones de este árbol combinado.

La revisión corrige la provisión para comprobar hashes y tamaños de los dos
JSON contra el recibo aprobado antes de cambiar credenciales o archivos, usando
los mismos bytes verificados al instalarlos. Un manifest de fuentes revisadas
no demuestra despliegue: deployed_commit permanece null. Cuando el predicado
de ruta no resuelve la autoridad legacy, se devuelve autoridad no resuelta con
una brecha explícita; no se inventa una denegación ni elegibilidad para aprobar.
No se añaden columnas, lecturas, permisos, roles ni herramientas.
