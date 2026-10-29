# Revisión del candidato de lecturas contra main — 2026-10-29

Estado: **candidato de código para PR de borrador; no listo para merge ni activación**.

Base revisada: `74598d1abdb883aca9bbfdcdbf41a2b67aacce58` (`main`).
Candidato: `217501f56e808c4f23051424035a761df63cc2a4`, rama
`feat/connector-reads-diagnostics-20261016`; ancestro común
`a983be79e1ad8a6b5aa56affe27a945160ac8166`. Divergencia comprobada: main tiene
123 commits exclusivos y el candidato uno. Rama aislada de revisión:
`feat/connector-review-20261029`.

## Dictamen y alcance

Sirve como base de consultas acotadas de SOLICITUD, presupuesto, CxC, Payment Run
y metadatos AMEX. No constituye todavía soporte integral de Batiseñal.
Runtime objetivo: owners del web `copa_telmex_dashboard.py`; frontera PostgreSQL,
sin puente Supabase nuevo. Nivel: `coded_not_wired`. Los owners financieros
existentes siguen siendo autoridad de lectura y de escritura; no se crea un
owner financiero paralelo ni se autoriza escritura de negocio.

Se ha trasladado el candidato al main inspeccionado conservando INFORME,
delegaciones, catálogo, servicio y controles de revocación. No se registra
ninguna herramienta nueva, no se crea una conexión ni se concede un permiso.

## Cambios necesarios para conciliarlo

- Payment Run: conflicto real con la extracción posterior de
  `_PAYMENT_RUN_SOURCE_SQL`. Se conserva la consulta común de main, la selección
  por fecha efectiva de las pagadas, los estados y los defaults de sus callers.
  La ampliación añade únicamente los filtros exactos, la opción de omitir DDL
  y la señal de importe fuente ausente en la consulta acotada.
- CxC: main ya agrega receipts de cobros parciales y conserva reversos/conflictos.
  Se elimina la segunda consulta de matches y la retención obsoleta por
  multiplicidad. El adapter usa los totales canónicos y conserva NULL para
  saldos desconocidos, con gaps explícitos. La regresión verifica 50 + 20 = 70,
  saldo 46, reverso a 50 y aislamiento de otra versión/objeto.
- Fixtures: se usa la constante SQL vigente de Payment Run; se evita agregar
  columnas ya existentes; se incluyen tablas canónicas de auditoría de versiones,
  pólizas y líneas en el PostgreSQL/WASM sintético. No se aplica esquema a una
  base externa ni se modifica el SQL del owner para satisfacer el test.
- Diagnóstico técnico: se auditan también las denegaciones iniciales de identidad
  y de composición asíncrona, sin inventar actor ni leer código antes de autorizar.
- CI: se añade el runner ampliado con PGlite 0.3.14 y cobertura acumulada al job
  existente, sin eliminar la aceptación nativa ni rebajar el umbral de 85%.
  La suite nativa original no descubría los nuevos tests; el pytest general
  excluye expresamente `tests/unit/readonly_gateway`.

## Fronteras comprobadas que siguen pendientes

| Componente | Evidencia actual | Paso mínimo que falta |
| --- | --- | --- |
| Autoridad financiera | Resolver async, ceilings del servidor, guards y nuevas sesiones; tests sintéticos de revocación | Binding autenticado de instalación/empleado/objetos/fiscalidad y auditoría durable, con recibos auténticos |
| Código y diagnóstico | Nueva entrada `TechnicalReads.call_async` espera autoridad y revalida antes/después de auditoría; la entrada sync rechaza resolver async | Binding autenticado de scope técnico propio; no copiar permisos financieros |
| Scope técnico | `CanonicalAuthorityResolver.binding` rechaza `technical.source`, fuera de `DATASETS` | Prueba positiva/negativa del owner técnico antes de declarar diagnóstico disponible |
| Desarrollo Agentius | Contrato local puro, sin worker ni persistencia | Adapter contra árbol/manifest vigente con recibos e idempotencia; no renombrar revisión a hash |
| PostgreSQL nativo | `initdb`, `pg_ctl`, `psql` y `/usr/lib/postgresql` ausentes en este entorno | Cluster exclusivamente desechable, driver real, rol mínimo, revocación concurrente y cancelación |
| AMEX | Getter/modelo original y cuerpos de guards ejercitados con fixtures; admisión de esquema explícitamente simulada | Esquema canónico completo y ORM/imports originales en PostgreSQL nativo; casos legacy/direct/contexto/empresa/tarjeta |
| Batiseñal | Ninguna prueba autenticada extremo a extremo ejecutada | Caso autorizado que vincule solicitante, contexto, fuentes y propuesta privada de respuesta |

La incompatibilidad de ejecución se corrige mediante `call_async`: usa el mismo
predicado de source_id/commit, espera la autoridad inicial y revalida antes del
allow y antes de entregar la respuesta. Cancelación, timeout, auditoría fallida
y revocación impiden entrega. La entrada sync sigue devolviendo
`ASYNC_AUTHORITY_REQUIRED` si se intenta saltar el resolver async. El resolver
canónico financiero no adquiere scope técnico: su uso con la nueva entrada devuelve
`NOT_FOUND_OR_NOT_VISIBLE`; añadir `technical.source` al binding financiero sigue
produciendo `AUTH_REQUIRED`. No se amplía DATASETS ni se aceptan grants del modelo.

El schema de herramientas Agentius Directo disponible en esta sesión confirma:

| Operación | Wire anunciado | Contrato local del candidato |
| --- | --- | --- |
| create | project, request_id, mode, base_sha, manifest_hash, objective, scope{read_files,write_files,checks}, story_ref/spec_ref opcionales en schema | project, objective, files, checks |
| start/resume | work_id, expected_contract_hash, request_id | work_id, expected_revision |
| artifact | work_id, artifact_id, cursor opcional | work_id, expected_revision, sha256 |

No se invocaron jobs ni se verificó el worker desplegado. El schema anunciado no
prueba su ejecución ni permite inventar hashes o artifact IDs.

## Unified Resources y separación de componentes

La revisión recibió como antecedente un paquete Agentius Unified Resources del
18/oct, `REVIEW_ONLY_NOT_INSTALLED`, SHA-256
`070b70882deec0474af693ed44c2642fb42eb49e9f129076fd7020e017dafcab`.
No se encontró el artefacto ni ese hash en los archivos de texto accesibles del
workspace. Su contenido e integridad no se verificaron aquí. No se instaló,
desplegó ni reconstruyó un adapter `resources_*`.

La integración debe separar: (1) owners/perímetro financiero de SamChat;
(2) binding de identidad y transporte; (3) recursos de código/plataforma y worker
Agentius. El paquete de infraestructura puede complementar (2)/(3); no sustituye
los owners financieros ni convierte estos contratos locales en herramientas live.

## Verificación reproducible

Python 3.12.14; entorno virtual desechable bajo `/tmp`, SQLAlchemy 2.1.6,
Node y PGlite 0.3.14. Sin entorno/credenciales de producción. Comandos principales:

```sh
export SAMCHAT_TEST_PGLITE_PATH=/tmp/samchat-connector-node/node_modules/@electric-sql/pglite/dist/index.js
/tmp/samchat-connector-venv/bin/python scripts/readonly_gateway/run_expanded_tests.py
/tmp/samchat-connector-venv/bin/python scripts/readonly_gateway/run_expanded_tests.py --legacy
```

- Candidato actualizado: **57/57 tests únicos pasan**, incluyendo autoridad técnica
  async y rechazo del contrato local por el proxy wire registrado.
- Cobertura de los siete módulos nuevos: **708/754 instrucciones, 93,90%**.
  No es cobertura de todo el repositorio ni prueba de imports/runtime nativos.
- Regresión comparable en el primer head `e3ffa92`, mismo entorno y harness de namespaces:
  main: 81 tests, 58 pasan, 3 fallos, 6 errores, 14 omitidos;
  candidato: 129 tests, 106 pasan, los mismos 3 fallos, 6 errores y 14 omitidos.
  Se compararon los nueve IDs: **ningún fallo nuevo observado**.
- Ocho fallos/errores heredados corresponden a identidad/rol/JIT del harness
  PGlite de la suite legacy; uno a `AUDIT_UNAVAILABLE` en el test de auditoría
  durable en este filesystem. No se modificaron guards para hacerlos pasar.
- Los 14 omitidos requieren el entorno de aceptación PostgreSQL nativo.
  La suite completa no está verde; esta comparación no reemplaza la aceptación.
- Pasan los cuatro scripts: `check-pr-quality-gate.py`,
  `check_runtime_packaging_consistency.py`,
  `check-registration-operational-surface.py`, `check-accepted-regressions.py`.
- Compilación y diff hygiene pasan. Black/isort aplicados al código nuevo y sus
  tests. No se ejecutó la suite completa de aplicación ni se acredita UAT.
- CI remoto y revisión del diff deben consultarse en el PR; esta evidencia local
  no los da por aprobados.

Los tres hashes canónicos se verificaron contra el registro antes de investigar.
**Canon unchanged:** esta preparación reutiliza owners e invariantes vigentes;
no modifica autoridad de negocio, datos, esquema productivo, despliegue ni
aceptación. No contiene cambios a los tres canons o su registro.

No hacer merge mientras falten las verificaciones anteriores y revisión del
alcance útil. Si se decide promover sólo la biblioteca financiera, acotar primero
ese alcance explícitamente; no presentarlo como cierre de Batiseñal. Revertir esta
propuesta de código no requiere tocar datos ni retirar grants, pues no los crea.


## Seguimiento del CI y del wire

El primer head `e3ffa92` pasó Assistant Scoped Gate, Browser UX Pilot y los jobs
de política y seguridad. El job unitario falló en la reproducción del inventario
estático de private_plugin: los siete módulos añadidos y las líneas/hashes de
owners no estaban reflejados en los archivos generados. Es un fallo causado por
este PR. Se regeneraron `route-inventory.json` y `route-matrix.csv` mediante
`python scripts/private_plugin/inventory.py`, preservando el generador, sus
exclusiones y los estados DISABLED. Resultado: 613 rutas, 578 montajes estáticos,
44 acciones canónicas, cero parse gaps. **135/135 tests aislados del plugin pasan**
en su entorno de dependencias fijadas. La conclusión remota del head actualizado
se conserva en el PR; no se presume por esta ejecución local.

La revisión del wire ejecuta el admission check del proxy histórico versionado
`tests/unit/readonly_gateway/fixtures/bridge/work_tools.py.fixture`. Las seis
operaciones locales create/start/resume/status/cancel/artifact son rechazadas por
campos desconocidos/faltantes, antes de acceder a sockets. Esa prueba coincide
con el schema anunciado en las herramientas disponibles; no demuestra identidad
con el worker desplegado. No existe aquí un recibo autenticado que permita
traducir revisión a `expected_contract_hash`, hash de contenido a `artifact_id`,
ni una selección `files` a permisos diferenciados read/write y request_id durable.
Inventarlos debilitaría autoridad e idempotencia. El adapter wire sigue pendiente
del árbol/manifest y recibos auténticos del executor; no se creó un sustituto.

Este seguimiento corrige ejecución async e inventario, sin registrar herramientas,
crear grants, conectar una identidad real, instalar PostgreSQL/infraestructura
ni afirmar aceptación nativa AMEX. Canon unchanged por los mismos motivos.
