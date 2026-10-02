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

- 179 unitarias/regresiones enfocadas aprobadas; 97% de cobertura combinada en
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
