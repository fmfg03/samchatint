# Dirección: reportes ejecutivos — evidencia sintética para revisión

Fecha: 2026-10-02. Base: PR452, `11ff4c4b828bec7df4e8b0b07e87d9184739e3bc`.
Estado: propuesta de código; sin merge, despliegue, consulta productiva ni UAT de negocio.

## Resultado y límites

`/direccion/inicio` usa el producto existente (`home_ui.py`, `direction_home.js`),
con ancho completo, Resumen / Presupuesto vs Real / Flujo de efectivo, selector
uno/varios/todos, brechas compactas y Sam contextual en un diálogo. Las cifras
de una celda se resuelven en el servidor contra el reporte firmado; el navegador
no envía importes autoritativos. Escenarios y exportaciones conservan el snapshot.
El contexto indica seleccionados / incluidos por el filtro / universo accesible.
Para SUPERADMIN se distingue «todos los torneos activos de la instalación» de una
cartera concreta; los cambios sin aplicar se marcan como pendientes.

Los hechos documentales ya no dependen de que exista un presupuesto aprobado.
`budgets.executive_facts` consulta el conjunto de UUID autorizado, deduplica por
identidad y reutiliza la base fiscal canónica y el resolutor de importe pagable.
Ejercido usa fecha de gasto. Comprometido y pagado usan la cohorte de solicitudes
creadas en el intervalo; no son flujo por fecha efectiva ni caja y no se suman al
ejercido. Las obligaciones son stock actual a próximos 30 días, no el periodo
histórico del selector. Calidad, moneda, reparto compartido y truncación conservan
brechas; un subtotal cubierto no representa toda la cartera.

La autorización explícita de Francisco de las 18:27 UTC permite a SUPERADMIN
leer los torneos activos del catálogo local de esta instalación. Los demás
perfiles conservan posición/cartera; una cartera explícita también limita la
selección de SUPERADMIN. Se revalida cada UUID para Sam y PDF/XLSX. No se crean
asignaciones, no se consulta un catálogo externo ni se amplía autoridad de
escritura. El esquema local actual no tiene un catálogo multi-organización;
antes de reutilizarlo en una instalación multi-tenant haría falta un predicado
explícito de organización. Las enmiendas de canon y sus hashes están propuestas
en esta misma revisión y requieren revisión humana antes de merge.

### Conexiones que NO se afirman completas

- Los layouts están integrados en pantalla y exportación, pero falta la
  correspondencia productiva validada entre conceptos y las partidas del libro,
  el plan mensual aprobado e ingresos atribuibles. Sus celdas conservan ausencia.
  No se asigna el total documental a «Costos Directos» por conjetura.
- CashFlow no lee ni distribuye saldos empresariales entre torneos. La fuente
  actual requiere conciliación, empresa/cuenta, clasificación y atribución.
  El contrato acepta periodos explícitos y existencias inicial/final, sin sumar
  saldos YTD, inventar años comparativos o convertir unidades dos veces.
- CxC conserva los requisitos de su lector canónico (atribución, versión y
  matching aceptado). CFDI sin atribuir no se convierten en cobranza. No se suman
  inversión, intercompañía o devoluciones como ingresos/gastos operativos.
- Las consultas documentales acreditan atribución por documento o cuenta canónica; no
  prometen cobertura de registros sin torneo ni consolidación por razón social.
- No se ha ejecutado SQL contra producción ni una base productiva clonada. Las
  pruebas verifican proyecciones, consultas parametrizadas y fronteras con dobles
  de prueba; conciliación SQL con datos reales y aceptación siguen pendientes.

## Fuentes de diseño y bloqueo de Library

IDs requeridos: `libfile_aa5bfbcf06a08191a96b7616d226fa26` (CashFlow),
`libfile_be84055da9a081919f126edb95eb9c5f` (Presupuesto vs Real),
`libfile_45dcea1ae05881918986bc256e15a364` (captura UAT).

Library reconoció los tres y preparó sus transferencias. La materialización local
falló para cada uno con salida 1: `library file transfer failed: download failed`.
Una nueva preparación y un reintento acotado dieron el mismo resultado. Ninguno
existe localmente. No se cambiaron permisos de red ni se eludieron restricciones.
Se utilizó el mapping inspeccionado y proporcionado por el padre/analista; NO se
afirma inspección local de originales. Las filas hijas son «Partida 1 / Partida 2»
y las de efectivo «Conceptos/Proyectos»: placeholders, no clasificaciones reales.
Los importes de los libros no fueron importados ni usados como fixtures.

## Evidencia visual

Todo importe, torneo y periodo de estas capturas y muestras es **sintético**.
«Antes» renderiza el código de PR452 con el mismo dataset de prueba; no es una
captura de producción ni sustituye el JPG de UAT pendiente de inspección local.

| Recorrido | Escritorio | Móvil |
|---|---|---|
| Antes, PR452 | [Antes](before-desktop.png) | [Antes móvil](before-mobile.png) |
| Resumen nuevo | [Resumen](after-desktop.png) | [Resumen móvil](after-mobile.png) |
| Presupuesto sin mapping | [Presupuesto](after-budget-desktop.png) | [Presupuesto móvil](after-budget-mobile.png) |
| Flujo sin fuente acreditada | [Flujo](after-cashflow-desktop.png) | [Flujo móvil](after-cashflow-mobile.png) |
| Contrato de presupuesto lleno sintéticamente | [Contrato](populated-budget-desktop.png) | [Contrato móvil](populated-budget-mobile.png) |
| Contrato de flujo lleno sintéticamente | [Contrato](populated-cashflow-desktop.png) | [Contrato móvil](populated-cashflow-mobile.png) |

[Sam contextual](after-sam.png). Las muestras llenas inyectan un mapping sintético
solo para comprobar representación y aritmética; no prueban conexión productiva.

[PDF con faltantes](synthetic-report.pdf) · [XLSX con faltantes](synthetic-report.xlsx)
· [PDF de contrato sintético](synthetic-populated-report.pdf)
· [XLSX de contrato sintético](synthetic-populated-report.xlsx).

Se inspeccionaron los píxeles de las vistas y páginas PDF, y se corrigió el ajuste
de cabeceras de la tabla impresa. [Comprobaciones visuales](visual-checks.json),
[aritmética y estructura de exportación](export-checks.json),
[accesibilidad WCAG 2 A/AA y 2.1 AA con axe-core](accessibility-checks.json).
Sin desbordamiento horizontal de página a 390 px; las tablas tienen desplazamiento
local. Sin errores JavaScript. Navegación de tabs por teclado, Escape y retorno de
foco de Sam comprobados. Cero infracciones automáticas en las vistas probadas;
esto no sustituye una auditoría manual completa de accesibilidad.

## Pruebas reproducibles

Python 3.12, Chromium local. La instalación nueva necesitó además `itsdangerous`
y `greenlet` para recolectar las pruebas. La descarga de Playwright falló por
dominio prohibido; se usó `/usr/bin/chromium` preinstalado.
Una ejecución conjunta de browser + pruebas async produjo 23 errores del runner
(`Runner.run() cannot be called from a running event loop`): el fixture de
Playwright síncrono mantiene su loop durante la sesión. Se ejecutan en procesos
separados como indican los comandos de abajo; no se atribuye ese fallo al producto.

- 188 unitarias/regresiones enfocadas aprobadas; 97% de cobertura combinada en
  `executive_facts.py` y `report_layouts.py`. Advertencias existentes de deprecación.
- 3 pruebas de navegador aprobadas: filtros múltiples, autorización contextual,
  escenarios/exportes, tabs, diálogo, foco y móvil.
- Verificación XLSX: filas 6–31, seis columnas, faltantes vacíos, subtotales sin
  duplicación, remanente y cashflow en miles sin doble conversión.
- `git diff --check`, `node --check` y hashes de canon verificados.

```bash
PYTHONPATH=src python -m pytest tests/unit/test_direction_report_redesign.py tests/unit/test_direction_home.py tests/unit/test_direction_home_routes.py tests/unit/test_direction_analysis_reports.py tests/unit/test_client_executive_service.py tests/unit/test_client_executive_auth.py tests/unit/test_direction_dashboard_scope_identity.py tests/unit/test_direction_dashboard_source_reconciliation.py tests/unit/test_direction_dashboard_account_truth.py tests/unit/test_direction_route_middleware.py -q --cov=samchat.budgets.executive_facts --cov=samchat.client_executive.report_layouts
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium PYTHONPATH=src python -m pytest tests/browser/test_direction_home_browser.py -q
PYTHONPATH=src python scripts/direction_report_visual_qa.py
```

Rollback de código: revertir el PR. No hay migraciones ni escrituras financieras
que revertir. El estado de CI remoto se registra en el PR; pruebas locales no
equivalen a despliegue, reconciliación productiva o aceptación del negocio.

## Correcciones posteriores a revisión

- CI del candidato ce0534e: todos los gates aprobados y 98% de cobertura de cambios.
  Se reparó compatibilidad de alcance y fuente con el plugin privado, conservando
  validaciones: 130 pruebas offline y 20 PostgreSQL aisladas aprobadas.
- Revisión Codex: estados terminales canónicos sin fecha de pago ya cuentan en
  compromiso/pagado; resolución de informe por enlace explícito, legado o cuenta
  con informe único. [Verificación SQL sintética](sql-link-checks.json) ejecuta
  el SQL real y la base fiscal en PostgreSQL efímero, comprueba precedencia,
  exclusión de cuentas ambiguas y de torneos ajenos. Reproducir con
  `python scripts/direction_report_sql_qa.py` y dependencias PostgreSQL opcionales.
- Revisión CodeRabbit: sin proyección válida no se emite una falsa brecha de
  conciliación. Un desfase real sigue anulando la proyección.
- Revisión móvil: cabecera compacta, procedencia y detalle de tarjetas desplegables,
  leyenda fuera del scroll y aviso visible de deslizamiento. Prueba a 390px exige
  primera cifra antes de y=800, leyenda legible, detalles expandibles y sin overflow.
  Se regeneraron e inspeccionaron capturas; axe-core permanece sin infracciones.

El SHA posterior a estas correcciones necesita su propio CI/revisión; consultar
la conclusión más reciente del PR, no extrapolar el verde de ce0534e.

Segunda revisión: el SQL usa el torneo efectivo del informe o cuenta canónica en
selección y filtro autorizado. `pagado_en` conserva pagos pese a estado mutable
cancelado/rechazado. Ambos casos pasan SQL PostgreSQL sintético; el rechazo sin
pago queda excluido. El comentario sobre carteras SUPERADMIN era un falso positivo:
el lector canónico ya consulta todas las carteras activas sin asignaciones; se
verificó con una regresión directa. No se amplió nuevamente la autorización.

Tercera revisión: el informe mantiene precedencia y la solicitud directa actúa
como fallback documentado; SQL sintético incluye ese flujo y excluye torneos ajenos.
Los permisos de fuente ahora omiten físicamente la consulta denegada: presupuesto
controla ejercido y Finanzas compromiso/pagado. Se verifican combinaciones de
denegación en lector, home y plugin. Contextos legacy escalar+lista se revalidan
con un único selector para Sam/PDF/XLSX. Las preguntas operativas sobre celdas
pasan el mismo guard de intención y responden que no se ejecutó acción alguna.

Cuarta revisión: el fallback de solicitud reconoce `solicitud_documento_id`,
`documento_id` y `gasto_generado_id`, priorizando enlace explícito y conservando
precedencia del informe. SQL PostgreSQL comprueba atribución, enlaces generados,
precedencia y exclusión de solicitudes ajenas. Compromisos reutiliza el conjunto
canónico `_BUDGET_COMMITMENT_DOCUMENT_STATES`, que incluye `enviado`; la solicitud
enviada sin evidencia de pago cuenta como compromiso, nunca como pagada.

Quinta revisión: solicitudes heredan torneo de su cuenta canónica solo cuando
no tienen torneo directo; selección y filtro comparten exactamente la expresión.
SQL sintético verifica precedencia y exclusión de cuentas ajenas. El plugin admite
SUPERADMIN sin carteras configuradas, conservando organización acreditada y
denegaciones explícitas (131 pruebas offline). Los exportes describen el ejercido
como base fiscal canónica documental, sin atribuirlo a un presupuesto; PDF/XLSX
verifican ese texto y sus artefactos fueron regenerados.

Sexta revisión: la consulta de obligaciones aplica el límite por torneo mediante
`row_number` particionado dentro del alcance autorizado. Un torneo truncado no
anula las cifras de otro completo; el agregado permanece incompleto. Regresiones
con límite pequeño verifican truncado/completo, dos completos que exceden juntos
el límite y torneo vacío. Pasan 245 pruebas de Dirección y lectores financieros
relacionados; el cambio no modifica píxeles ni formatos de exportación.

Séptima revisión: obligaciones usa el mismo torneo efectivo (documento o cuenta)
en autorización, partición y resultado. Gastos y solicitudes documentales también
particionan el límite por torneo; cada fuente conserva su propia completitud.
Pasaron 248 pruebas relacionadas. PostgreSQL efímero ejecutó los dos SQL reales
con dos torneos y límite reducido, verificando que ninguno consume el cupo del
otro; la atribución por cuenta y prioridad del torneo directo tienen regresiones.

Octava revisión: el lector de obligaciones carga explícitamente el torneo diferido
de la cuenta. Cada fuente documental usa su propio savepoint; un fallo no oculta
hechos de la fuente independiente. Contextos/análisis que superarían 90.000
caracteres usan una firma compacta SHA-256 del contenido serializado exacto,
transportado aparte como cadena. Se verifican firma, vencimiento, hash, identidad
y alcance actual antes de confiar en los datos; no se requiere un nuevo almacén.
Regresiones: contexto originalmente mayor de 100.000 caracteres, alteración,
ausencia, vencimiento, Sam/escenario/PDF/XLSX y transporte intacto en navegador.
Pasaron 252 pruebas funcionales, 4 de navegador, 131 del plugin y 20 PostgreSQL.

Novena revisión: el enlace lateral del gasto es opcional; una cuenta con torneo
acreditado aporta atribución aunque no exista informe/solicitud. SQL real verifica
ese caso y excluye la cuenta ajena. El plugin pagina catálogos de 25 elementos por
tipo con cursor ligado al digest del alcance; una modificación obliga a reiniciar.
Los resúmenes conservan todos los indicadores y cobertura del conjunto completo.
Listas de IDs grandes se omiten explícitamente, con conteos, flags de completitud
y digest en `scope_manifest`; el recibo de auditoría incluye ese manifiesto.
Se validan todos los UUIDs incluso cuando no se enumeran en la respuesta. La
regresión recorre 1.001 torneos y 1.001 carteras sin pérdida ni duplicación.
Pasaron 252 pruebas funcionales, 132 del plugin, 20 PostgreSQL y el fixture SQL.

Décima revisión: las proyecciones canónicas por torneo se ejecutan con concurrencia
acotada a cuatro sesiones independientes, transacciones de solo lectura y el motor
ya configurado. No comparten AsyncSession ni crean conexión/configuración nueva.
Se mantienen cálculos, orden y permisos; un fallo de sesión conserva los hechos
independientes. Es concurrencia acotada de lectores existentes, no una consulta SQL
vectorizada ni una medición de latencia productiva. La regresión de 1.001 torneos
verifica paridad con ejecución serial, límite, cierre, fallo y denegación de fuente.
`scope_manifest` y `pagination` son obligatorios en sus contratos de salida.
Pasaron 255 pruebas funcionales (97,35% de cobertura de los módulos medidos), 133
del plugin y 20 PostgreSQL. QA visual/exportes sintéticos pasó sus 11 comprobaciones,
sin errores JS; ambos PDF de cuatro páginas conservan texto dentro del papel.
Los reportes explicitan lecturas independientes con cortes individuales.

### Ajuste local posterior a 6654127 — sin publicación

Los P2 `4170557362` y `4170557367` tienen correcciones locales para revisión:

- SQL canónico agrupado en lotes de 25 mediante `UNION ALL` parametrizado, con
  una sesión/transacción de solo lectura por lote (máximo cuatro activas). No se
  sustituyen fórmulas, filtros, aliases, tipos, orden ni límites de los lectores.
  Lecturas idénticas comparten resultado dentro del lote. Un error revierte su
  savepoint y divide el lote para conservar las otras fuentes; la cancelación
  cierra tareas y sesiones. Se mantienen subconsultas por alcance: no se afirma
  una agregación financiera nueva ni un único escaneo físico de todas las tablas.
- SOUL agrupa las lecturas del dataset con los mismos límites **por solicitud**,
  incluso con un torneo dominante. El catálogo canónico se reutiliza dentro de
  la petición; se omiten llamadas de detalle opcional que no aportan los dos
  conteos usados por la portada. Se mantiene el dueño SOUL/dossier y el guard
  UUID/nombre/edición. No se cambia el límite preexistente de su catálogo remoto.
- El catálogo privado aplica `LIMIT/OFFSET` en PostgreSQL y transmite páginas de
  hasta 25 torneos/25 carteras, más metadata acotada. La autorización conserva
  posición, denegaciones y prueba de organización completa; esta última requiere
  el binding explícito documentado en `docs/private-plugin/local-read-verification.md`.
  La integración productiva del plugin permanece sin acreditar.

[Medición SQL sintética](batch-scaling-checks.json): con 1 / 25 / 100 / 1.001
registros, el lector SQL de prueba ejecuta 2 / 2 / 8 / 82 SELECT agrupados y devuelve
2 / 26 / 104 / 1.042 filas. Enumerar y revalidar el catálogo hace respectivamente
2 / 2 / 8 / 82 consultas y devuelve 4 / 100 / 400 / 4.004 elementos de catálogo
(sumando carteras, torneos y revalidación). El digest/conteo todavía recorre el
alcance dentro de PostgreSQL; la medición no confunde filas transferidas con
filas examinadas internamente ni afirma latencia productiva. Las cifras de SELECT no incluyen los comandos
SAVEPOINT/RELEASE que aíslan cada lote ni BEGIN/COMMIT de sus transacciones.

Reproducir: `python scripts/direction_batch_sql_qa.py` con las dependencias
PostgreSQL opcionales, y `pytest tests/unit/test_direction_read_batches.py`.
La QA usa PostgreSQL 16 efímero por socket local, datos sintéticos y TCP desactivado.
También verifica digest idéntico al contrato Python, cambios de asignación,
exclusión ajena/inactiva, tipos SQL, aislamiento y prohibición efectiva de escribir.
**Canon unchanged:** transporte/lectura de los mismos dueños; no cambia autoridad,
contabilidad, significado de métricas, persistencia ni despliegue.

Validación local de este ajuste: **248 pruebas funcionales + 173 de los dueños
Presupuestos/CxC**, **135 offline del plugin**, **20 PostgreSQL del plugin** y
**4 de navegador**, todas aprobadas. Los nuevos módulos de transporte alcanzan
95% de cobertura cada uno (96,51% combinado con los otros módulos medidos).
No se publicaron commits, comentarios, artefactos Library ni cambios de estado
de PR para este conjunto local. Los checks remotos de 6654127 no se atribuyen
a estas modificaciones aún no publicadas.

### Reconciliación local sobre main/PR454 — 2026-10-03

Base solicitada: `e73d55b48794be53a868404a1c897dc7c8a9636e`, que contiene PR454
(`d5928eeca38d3788436f2f1d8ecc6af3608724fb`). Worktree aislado en
`/workspace/samchat-executive-reports-local`, rama local
`local/direction-p2-reconcile-e73d55b`. La copia anterior permanece intacta.
Se aplicó el diff conservado sin conflictos, excepto que los dos inventarios se
regeneraron desde esta base para preservar los hashes/rutas de PR454. Su código,
sus tests y sus capturas no tienen modificaciones locales.

Validación reconciliada: **528 pruebas funcionales**, incluyendo las 107 de
PR454; **135 del plugin**, **20 PostgreSQL**, **4 de navegador**, y QA de lotes/
paginación con PostgreSQL efímero: aprobadas. Cobertura dirigida 96,51%; los dos
módulos nuevos conservan 95% cada uno. Canon y registro sin cambios.

Incidencia de instrumentación conservada en los logs: medir por nombres de
paquete produjo `TypeError: 'InternalTraversal' object is not callable` en la
prueba SQL de PR454. También se reprodujo en la base e73d55b limpia con esos
mismos argumentos de cobertura, sin estas correcciones. La suite sin cobertura
pasó; midiendo `--cov=src/samchat` y filtrando el reporte a los mismos cinco
archivos mediante una configuración local, pasaron las 528 pruebas y el umbral.
No se omitió ningún test ni se alteró el código de PR454 para obtener ese resultado.
La causa interna de SQLAlchemy no se declara demostrada; la incidencia queda
asociada a esa modalidad de instrumentación y es reproducible con los logs.

Preparación exclusivamente local: sin push, comentarios GitHub, resolución de
hilos, cambios de auto-merge, merge, despliegue ni publicación de artefactos.
El bloqueo de publicación por auto-merge/canon pendiente no fue eludido.

### Aprobación humana y aclaración documental — 2026-10-03

Francisco aprobó las definiciones documentales de esta versión tras revisar el
criterio y el ejemplo explícito: solicitud de septiembre pagada en octubre aparece
pagada al consultar septiembre hoy, sin representar salida bancaria de septiembre.
Evidencia transmitida por el padre tras leer los mensajes originales:
`Sentinel_d9cbac39a4608191aead631e0f2af2b9` (definiciones y solicitud de revisión)
y `Sentinel_84e2fa86c93081919613ba94bde5bd08` (respuesta: «Aprobado»).
La revisión humana pendiente de esas definiciones queda satisfecha. La propiedad
de la instalación ya estaba confirmada; no se amplían permisos ni se cambia la
base de cálculo. Se añadió la aclaración visible a Resumen y se precisaron las
mismas definiciones compartidas por Sam y los exportes.

La publicación de las correcciones fue autorizada de nuevo mediante
`Sentinel_b11de76fc740819183049b400b4a3849` / `Sentinel_1f450b8d8614819191a99108fb1b5bd3`.
El merge de PR453 fue autorizado por Francisco en
`Sentinel_4d68ddcb74288191ba30452e48656df1`; queda sujeto a checks y revisión final.
Este turno no autoriza despliegue. El auto-merge configurado por el usuario no se
modifica. No se declara revisión limpia ni merge por anticipado.

La aclaración aprobada pasó 130 regresiones de Dirección/exportes, las 4 pruebas
de navegador y las 11 comprobaciones visuales. Se inspeccionaron nuevamente
capturas desktop/móvil y los límites de texto de ambos PDF de cuatro páginas.
Sin errores JavaScript ni desbordamiento de página; las capturas y muestras de
exportación fueron regeneradas con datos exclusivamente sintéticos.
