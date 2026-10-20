# Presupuestos: distribución conservando autorización e historial

Fecha: 2026-10-14. Candidato aislado sobre `fmfg03/samchatint main` SHA
`68e5ada6fb5c901ab75e125b95db731f6f3df50c`; publicación preparada sobre
`d8c9fda23ed1b1bb1a68a08bc524fedbdb6d4e5f` (#486, sin archivos solapados).
Sin datos productivos, despliegue ni aceptación de negocio. Francisco autorizó
la corrección de distribución y lectura en tableros; no otros flujos.

## Aprobación registrada y cierre del candidato

Estado: **candidato cerrado como paquete revisable; reglas aprobadas por
Francisco; commit, push y PR autorizados; sin merge, despliegue ni UAT productiva**.

Evidencia de aprobación, 14 de octubre de 2026:
`Sentinel_e34dca62ad888191ae00a160c9ee5b09`, Francisco: **«Aprobado»**.
La respuesta aprueba las reglas que Chatty presentó para cerrar el candidato:

> Una revisión de distribución puede sustituir al presupuesto activo, incluso
> congelado, únicamente al aprobarse con las mismas partidas e importes y con
> las semanas conciliadas al total. El original conserva su historial; otro
> ajuste requiere otra revisión. Cada semana se asigna al mes en que empieza;
> el acumulado incluye completo el mes seleccionado. Las 26 categorías fijas
> quedan en N/D hasta que Finanzas confirme qué partidas corresponden a cada
> una. La tabla por partidas reales sí muestra los periodos. Aún no está
> publicado ni desplegado.

Se cotejó el diff funcional con estas reglas y con el paquete Library previo:
los 17 archivos de código/tests y sus hunks permanecen idénticos. La selección
del sucesor exige linaje explícito, misma edición y estado aprobado/congelado;
la aprobación compara partidas/importes protegidos y concilia el gasto por
partida. Las protecciones de fuente e historial, el mes por inicio de semana,
el acumulado con el mes completo y la separación de plantilla fija frente a
partidas reales coinciden con lo presentado. No se encontró desviación de
política ni se modificó la implementación en este cierre.

Autorización posterior de Francisco, `Sentinel_a80479c5cb1081919315d4fa293a5777`
(2026-10-14, «Autorizado»), incluye commit, push, PR, seguimiento de CI hasta
resultado terminal y correcciones dentro del alcance aprobado. No incluye
merge, despliegue, sustitución económica, retiro histórico ni datos productivos.
El adaptador de las siete pruebas PostgreSQL usa PGlite local o el PostgreSQL
efímero del job existente de CI, exclusivamente con `GITHUB_ACTIONS=true` y
su DSN ficticio exacto; cada prueba queda en un esquema UUID propio.
La rama asyncpg requiere todavía verificación real en CI; revisión independiente
por inspección sin bloqueadores. No se alteran workflows ni gates.
El crosswalk de Finanzas, el caso real y UAT siguen pendientes. La aceptación
de estas reglas no acredita aceptación del comportamiento desplegado.

## Resultado y recorrido

La carga de totales anuales no implica calendarización. El autorizado anual
sigue visible cuando no hay plan, pero los periodos se muestran **pendientes**.
Un plan no vacío que no concilia bloquea aprobación/congelación; no se fabrica
distribución uniforme. La versión económica estándar puede seguir aprobándose
sin plan, con su brecha visible: el canon no exige periodificar todo presupuesto
anual. La revisión creada específicamente para completar distribución sí debe
conciliar todas las partidas de gasto antes de aprobarse.

En Presupuestos, una persona con los permisos existentes de creación y edición
puede confirmar **Completar distribución en nueva versión**. La acción copia
la edición completa a un draft específico de distribución, conserva todas sus
partidas, importes, fases, identidades, cuentas, evidencia y planes originales,
y registra el linaje en la auditoría existente. La copia es atómica y reutiliza
el draft pendiente de esa fuente. No cambia ni desactiva la versión original.

En esa revisión sólo se editan semanas de gasto. Importe anual, clasificación,
partidas e ingresos están protegidos también en backend; no basta con el
readonly del navegador. Se conservan incluso ingresos heredados en planes de
gasto antiguos. Reimportar o agregar partidas está bloqueado. El formulario no
genera semanas uniformes y el envío de campos vacíos no certifica un plan cero.

El actor puede enviar la revisión a aprobación mediante el lifecycle canónico.
Al aprobar se comparan **todas** las partidas con su fuente, no sólo el total
global; se comprueba linaje, edición, ausencia de sustitución concurrente,
ingresos intactos y plan de gasto completo, no negativo y conciliado por línea.
El servicio conserva locks de versión/partidas y serializa con la fuente.
El original sigue seleccionado hasta aprobar la revisión. Luego se selecciona
el sucesor explícito aprobado, incluso si el original está frozen. Otra
approved arbitraria no desplaza frozen por su sola fecha.

Los dos selectores anteriores tenían prioridades contradictorias. Ahora
comparten una resolución: frozen antes de approved para versiones económicas;
sucesor aprobado/congelado sólo con linaje explícito de distribución y misma
edición. Sibling aprobado ambiguo produce error, no selección inventada. Se
conserva el acceso explícito a versiones históricas. UI distingue borrador,
enviado, cerrado, activo e histórico y oculta las transiciones protegidas.

Las versiones que son fuentes de revisiones conservan su historia: no pueden
reforecast/cerrarse en sitio. Tampoco se reabre o cierra una revisión de
distribución aprobada; los ajustes de semanas requieren otra revisión. Un draft
de revisión puede cerrarse para cancelar su preparación. Esta protección no
concede privilegios ni libera indiscriminadamente frozen.

**Límite del recorrido:** una futura sustitución económica del presupuesto,
retiro de una fuente histórica o cambio de importes requiere diseño separado.
Crear cualquier versión económica approved no garantiza reemplazar un root
frozen. Este candidato no introduce ni promete esa activación económica.

## Lecturas y conservación financiera

El snapshot ejecutivo consume el plan de las mismas líneas y versión validadas
por su guard de torneo. Expone por partida: autorizado anual, estado de plan,
mes de corte y acumulado. Un plan ausente, inválido o desajustado mantiene los
importes por periodo desconocidos, también para autorizado cero. Un plan
explícitamente conciliado acredita las semanas sin asignación en cero.

Las semanas 1–52 se agregan al mes de su **fecha de inicio**, como ya hacía la
UI de Presupuestos. Los límites se comparten: semana 1 parcial hasta antes del
siguiente lunes y semana 52 hasta el 31 de diciembre. Mes y acumulado incluyen
las semanas asignadas al mes completo de corte, no un prorrateo hasta el día
de consulta. No se divide por doce ni se confunden números de semana con mes.

Dirección muestra una tabla adicional de **partidas canónicas reales**, junto
a sus estados. La misma fuente aparece en PDF y XLSX. La plantilla fija de 26
renglones conserva sus campos sin mapping como N/D, sin atribuir por semejanza
de nombres, sin inventar ingresos y sin fabricar variaciones contra hechos que
carecen de correspondencia. El autorizado anual permanece anual.

La revisión no duplica conciliaciones, pólizas, cobros ni CFDI. La fuente
financiera sigue siendo la versión original. Actuals heredados remapean IDs de
partida sólo para proyección; conservan IDs de movimiento/posting. AR usa la
raíz para links, candidatos y collection matches, incluso si el filtro no
devuelve partidas de ingreso, y mantiene sus identidades financieras. La
vinculación canónica de CFDI resuelve una partida clonada a la raíz acreditada,
validando versión, edición e identidad; no confía únicamente en metadata.
También el export de ingresos consulta links/candidatos de la raíz.

Se corrigió otra inconsistencia relevante del lifecycle: el listado de versiones
ya no multiplica COUNT/SUM al tener varias aprobaciones de una versión.
La última fecha de aprobación usa subquery; el agregado financiero sólo une
líneas.

## Archivos y superficies

- `src/samchat/budgets/distribution.py`: estados, semanas, proyección, selector,
  copia exacta atómica, linaje y protección/validación.
- `src/samchat/budgets/service.py`: selección común, linaje en versiones,
  aprobación, guards de edición/importación y lectura del plan ejecutivo.
- `src/samchat/budgets/tournament_upload.py`: no reimportar una revisión.
- `src/devnous/gastos/routes/admin_budget_routes.py`: nuevo POST
  `/admin/presupuestos/versiones/{version_id}/distribucion`, confirmación y
  permisos existentes, estado y recorrido UI, raíz en export de ingresos.
- `src/devnous/gastos/routes/admin_budget_ui.py`: semanas compartidas, estados
  pendientes, importe protegido y edición sólo de semanas en la revisión.
- `src/devnous/gastos/services/cfdi_income_bridge_service.py` y
  `src/samchat/ar/service.py`: fuentes e identidades financieras heredadas.
- `src/samchat/client_executive/home.py`, `home_ui.py` y
  `src/samchat/executive/exporter.py`: lectura mensual por partidas y artefactos.
- `docs/product/presupuestos-route-policy.md`: inventario de ruta candidata.
- Tests nuevos: `tests/unit/test_budget_distribution_revisions.py`,
  `tests/integration/test_budget_distribution_postgres.py` y su bridge PGlite.
  Fixtures existentes adaptadas al nuevo read de identidad financiera y ruta.

No nuevas tablas, migraciones, DDL de startup, permisos ni endpoint legacy.
La copia nueva no llama ensure_schema: usa las tablas existentes. Se eliminaron
ensure_schema anidados dentro del reemplazo de plan para no hacer commits
intermedios entre plan, allocations y auditoría.

## Validación y revisión

Resultado final: **437 tests aprobados** (incluye 29 unit tests nuevos y 7
PostgreSQL/WASM), nueve warnings heredados de deprecación, sin fallos. Cobertura
de instrucciones nuevas/modificadas: **86,14 % (317/368)**; módulo nuevo de
distribución: 86,36 %. `git diff --check` y compilación focal limpios.
La medición definitiva es secuencial y usa `.coverage-final-solo`; sustituye
una medición intermedia invalidada por solapamiento de archivos de cobertura.

Recibos de ejecución están en `/workspace/budget-incident-20261014/`:
`candidate-final-tests.txt`, `coverage-candidate.json`, `changed-coverage.json`
y `candidate.diff`. La última corrida y cobertura se registran en el cierre de
ese directorio; no representan CI global ni aprobación de release.

Las pruebas usan entorno vacío, columna de recibos ORM explícita para evitar
probe de DB, sesiones ficticias o PostgreSQL/WASM desechable **en memoria**.
No DSN, credenciales, .env, servidor o datos productivos. El runner de evidencia
`run_isolated.py` evita __init__ de aplicación con namespaces; carga servicios
reales. Esto no prueba startup completo ni autenticación productiva.

Regresiones ejercidas: carga anual sin plan, importación protegida, cero
explícito frente a ausencia, planes desajustados, campos protegidos en servidor,
recorrido completo desde frozen, reintento de creación, rollback de cada insert
ante fallo, intercambio de importes entre partidas con mismo total global,
fuente y revisiones aprobadas históricas, ingresos legacy intactos, selección
frozen/approved y ambigüedad, límites semanales incluyendo bisiesto/semana 52,
mes/YTD, raíz AR con/sin líneas, rechazo de fuente extranjera, ciclos, UI y
PDF/XLSX sin cero ficticio, autorización y confirmación de la nueva ruta,
múltiples aprobaciones sin duplicar autorizado.

Revisión independiente sin bloqueadores pendientes: agente `design_review`.
Ejecutó 23 unit tests y 6 PostgreSQL/WASM, incluida la rama de aprobación
estándar. Los tests posteriores de mensajes adicionales y rollback de monto
más plan pertenecen a la corrida del agente principal. Sus hallazgos sobre fuente mutable, export,
AR sin líneas, linaje y estados UI fueron corregidos. No editó código.

PostgreSQL/WASM acredita sintaxis y persistencia/rollback reales; su bridge es
secuencial y no acredita carreras multiproceso, deadlocks bajo carga ni runtime
SQLAlchemy/asyncpg desplegado. Health, release, revisión de datos de la edición
real y Finance UAT permanecen pendientes.

## Decisión de negocio pendiente sólo para la plantilla fija

Propuesta concreta: Finanzas aprueba un crosswalk por identidad
`torneo + edición + concepto + fase + dirección → hoja/row_id de plantilla`.
Cada partida debe asignarse a una sola hoja/leaf; varias partidas sólo se suman
en una leaf si esa correspondencia fue aprobada. Padres y remanente se calculan
una sola vez; partidas sin correspondencia quedan fuera de esos totales y con
brecha visible. Los ingresos y el ejercido requieren correspondencia y fuente
propias. No usar nombres aproximados ni repartir partidas entre las dos
subfilas genéricas. Hasta recibir ese crosswalk, se pausa **sólo la conexión a
esas 26 categorías**, manteniendo operativa la tabla mensual canónica.

Proyecto/pantalla/versión reales siguen pendientes. No se atribuye ningún
commit desplegado ni se asegura que este candidato explique cada registro del
incidente del 14 de octubre.

## Canon y alcance de la aprobación

`Canon updated`: las reglas aprobadas textualmente se incorporan al canon de
producto y al de ingeniería con fecha, motivo, evidencia y límites de aprobación.
Se refrescan sus hashes en el registro. El sweep permanece íntegro: describe
el inventario histórico de instalación y este candidato no acredita una nueva
observación productiva. La aprobación del cierre y la autorización posterior
de publicación quedan identificadas arriba.

> Una revisión de distribución conserva las partidas, importes autorizados e
> identidad financiera de su versión fuente en la misma edición. Sólo modifica
> semanas de gasto mediante autoridad presupuestal existente, confirmación y
> auditoría. La fuente permanece vigente hasta aprobar una revisión completa y
> conciliada por partida. El sucesor aprobado se selecciona por linaje explícito;
> no por reemplazo indiscriminado de frozen. Fuentes y distribuciones aprobadas
> conservan historia y no se reabren o retiran mediante este recorrido. Ausencia
> de plan permanece desconocida; no genera distribución uniforme ni cero
> autorizado por periodo. Dirección consume los periodos de la versión vigente
> dentro de su alcance y conserva las correspondencias ejecutivas no aprobadas
> como pendientes. Sustitución económica y retiro histórico requieren un diseño
> y autoridad separados.

No webhook, PR485, sugeridor contable ni cambios en negocio forman parte del
diff. Antes de cualquier release: obtener su autorización específica,
confirmar el crosswalk si se desea la plantilla fija, revisar el diff y
efectuar validación con el torneo/versión concretos. Ninguno de esos efectos
se deduce de la aprobación del cierre del candidato.

## Seguimiento de publicación y CI

PR #487, primer head `07998cc71ad561f1fd0bdac5bd7d5ac3f607be93`.
La primera ejecución de CI detectó inventario estático desactualizado:
453→454 archivos fuente y 590→591 rutas. Se regeneran JSON/CSV con
`python scripts/private_plugin/inventory.py`; únicamente cambian las rutas
de `admin_budget_routes.py`, sus líneas/hashes y evidencias derivadas.
No cambia el catálogo de 44 acciones ni se habilitan herramientas del plugin.
Workflow, gates y baselines de fallos permanecen intactos.

## Continuación autorizada — 2026-10-16

El usuario autorizó terminar y fusionar el PR #487; las limitaciones de publicación anteriores describen el punto de control del14 de octubre. Se corrigió el P1 pendiente de CxC/aviso semanal conservando raíz financiera y calendario de revisión, y se integró main03fa1fece. La integración exacta de los canons recibió revisión humana explícita adicional; conserva ambos textos aprobados. Cierre local:634 pruebas PASS, incluidas7 PostgreSQL15; cobertura322/376=85.64%. Ver `budget-distribution-20261016-merge-report.md`, validación independiente y evidencia asociada. Controles GitHub nuevos pendientes al publicar. Sin migración, datos ni despliegue productivo; Finance crosswalk y UAT siguen separados.

Compatibilidad final AMEX: el selector y validación de partidas excluyen revisiones de distribución, como ya exige el guard financiero canónico. Doce regresiones nuevas y dos pruebas PostgreSQL del owner AMEX. Resultado integrado648 PASS, cobertura323/377=85.68%, validación independiente CLEAN_WITH_NOTES. El headb116 previo y sus634 pruebas permanecen como evidencia histórica. Sin nueva regla de negocio, autoridad, canon o esquema.
