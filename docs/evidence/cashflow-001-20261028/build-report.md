# CASHFLOW-001 — Informe de construcción

2026-10-28. Historia y especificación aprobadas en la conversación.
Implementación local preparada para revisión del diff. **La verificación general
mantiene los límites de tipado indicados abajo; el bloqueo del SDK ya está cerrado.**
Sin commit, push, PR, merge, despliegue, migraciones ni cambios financieros.

## Resultado

Las dos vistas y Excel usan una proyección compartida: entradas, salidas,
variación, orígenes/destinos, detalle por movimiento y pendientes separados.
El reporte conserva apertura/orígenes/aplicaciones/cierre, moneda y período.
Saldo sin apertura acreditada permanece N/D; variación no se llama disponible.
Los cálculos usan Decimal, identidades únicas y año-mes. La agrupación no altera
el total; cuenta/proyecto/moneda conservan su universo. El detalle se pagina sin
limitar los totales y mantiene su tamaño de página y filtros.

Cobranza, factura y presupuesto no crean otra entrada bancaria. Pagadas no
reaparecen como obligaciones; enviadas están separadas de aprobadas/en proceso.
Vencidos y sin fecha permanecen visibles. Los estados documentales actuales no
fabrican pendientes históricos: esa proyección es N/D con fecha y motivo.
Planes vacíos o parcialmente distribuidos no se convierten en cero.

También se corrigió el consumidor `cashflow.statement`: efectivo real separado,
N/D preservado, monedas y años independientes, saldos sin sumarlos. El resumen
del asistente denomina correctamente la variación bancaria del período.

## Base y conservación de trabajo

Candidato: `/root/samchat/.worktrees/cashflow-001-20261028`.
Base inicial `ab29ccc64`; base final
`0f47e7b8cde0dc1959bb89f2f161d2341e99180b`, correspondiente al directorio servido
configurado al cierre: `gastos-prod-0f47e7b8c-copa-budget-load`.
La base avanzó durante el trabajo. Sus cinco archivos de presupuestos/Copa se
conservaron mediante checkout del nuevo commit, sin solapamiento con el diff de
cashflow. Se repitió la aceptación. No se editó el directorio servido ni el
candidato multiempresa existente. Los cambios ajenos del checkout principal se
preservaron; sólo se actualizó allí el estado de aprobación de esta spec.

Los owners multiempresa se reutilizaron mediante importación **sólo de pruebas**
en un proceso separado. Sus guardas y hashes constan en el manifiesto; no se
copió su diff ni se activó multiempresa. Los tres canons conservaron sus hashes.

## Archivos

Código:

- `src/samchat/cashflow/{service.py,admin_ui.py,__init__.py}`: fuentes,
  proyección, contexto, filtros, HTML y XLSX.
- `src/devnous/gastos/routes/{user_routes.py,admin_routes.py}`: únicamente
  los tres handlers existentes de cashflow; eliminación del ensamblado duplicado.
- `src/samchat/executive/template_reports.py`: únicamente
  `build_cashflow_statement_report`, consumidor efectivo T04 registrado antes
  de editar; no cambia el builder de presupuestos.
- `src/samchat/assistant/finance_read_answer.py`: dos etiquetas en consumidores
  registrados. El contrato de Dirección `client_executive/report_layouts.py`
  sigue intacto y mantiene N/D cuando falta su evidencia de clasificación/saldo.
- Inventario y matriz en `docs/private-plugin/`, regenerados por el script oficial.

Pruebas actualizadas: planning read model, admin UI/route contract, estados y
duplicados, executive templates, weekly-null y respuestas financieras.
Pruebas añadidas: `test_cashflow_report_ui.py`, `test_cashflow_route_equivalence.py`,
`test_cashflow_source_loading.py`, `test_cashflow_statement_consistency.py`,
`tests/integration/test_cashflow_sources_postgres.py` y
`tests/browser/test_cashflow_report_browser.py`.
El manifiesto enumera los paths individuales y SHA-256 de runtime.

## Comandos y resultados

Desde el candidato, con el entorno existente `/root/samchat/.venv/bin/python`:

1. `CASHFLOW_TEST_DSN=postgresql+asyncpg://postgres@127.0.0.1:38397/cashflow001 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /root/samchat/.venv/bin/python docs/evidence/cashflow-001-20261028/verify_acceptance.py`:
   **172 aprobadas**, incluidas **4 PostgreSQL reales aisladas** y **2 navegador**
   a 1440/390 px. **90.67%** de cobertura del conjunto medido; service cashflow
   88%, UI 100%, template reports 89%. No es cobertura de todo el repositorio.
   El driver importa primero `devnous.gastos`, como el runtime, por un ciclo de
   importaciones preexistente de gastos/presupuestos; no se reparó ese otro módulo.
2. `PYTHONPATH=src /root/samchat/.venv/bin/python scripts/private_plugin/inventory.py`:
   **609 rutas, 574 live-static, 44 acciones, cero parse gaps**.
3. `test_inventory.py` mediante el guard offline del runner oficial:
   **9 aprobadas**, incluida reproducibilidad del inventario final.
4. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/cashflow001-mcp129:src /root/samchat/.venv/bin/python scripts/private_plugin/run_offline_tests.py`:
   **135 aprobadas, cero errores**, con el pin **mcp==1.29.0** instalado en el
   directorio temporal expresamente autorizado. Incluye las nueve pruebas de
   inventario. Se añadió allí su dependencia faltante `httpx-sse==0.4.3` y se
   verificaron los requisitos activos del SDK. La `.venv` compartida conserva
   **MCP 2.3.0**, sin conflictos en `pip check`. El fallo anterior con 2.3.0 queda
   sólo como historial en `private-plugin-mcp230-incompatible.log`.
   Resultado actual: `private-plugin.log`; procedencia: `mcp129-receipt.json`.
5. `/usr/bin/flake8 --max-line-length=88` sobre cashflow y las pruebas nuevas:
   aprobado. Estilo de las funciones tocadas en rutas/consumidores y compilación:
   aprobados; el análisis de snippets excluye nombres externos a esos snippets.
   El archivo completo de template reports conserva tres E501 preexistentes en
   el builder de presupuesto intacto; el test de respuestas conserva dos E501
   preexistentes fuera de sus aserciones modificadas.
6. `git diff --check`: aprobado. Comparación AST contra HEAD: sólo los handlers
   y consumidores registrados cambiaron en sus respectivos módulos.

Revalidación con las herramientas proporcionadas por el usuario:

- **Black 26.10.1 / isort 9.0.2: aprobados** en 14 archivos completos del alcance
  y 10 funciones extraídas de módulos grandes. No se reformatearon rutas ajenas.
  Se comprobó equivalencia AST del cambio de formato; recibo `format-receipt.json`.
- **mypy 2.4.0: código propio aprobado**, mediante
  `mypy --follow-imports=skip --ignore-missing-imports src/samchat/cashflow /tmp/cashflow001_statement_types.py`.
  Se corrigieron anotaciones e inferencias de DTO y estrechamiento de fechas,
  sin cambiar reglas financieras, guardas ni añadir supresiones en el código.
  La función de statement extraída es la función aprobada, no una implementación
  alternativa; el análisis del módulo completo confirma cero errores en ella.
- Sin excluir metadatos externos quedan **5 import-untyped**: tres módulos
  opcionales de empresa y dos imports de openpyxl. No se declara tipado general limpio.
- En los cuatro módulos grandes de rutas/consumidores se registran **143
  diagnósticos**, **ninguno en las funciones modificadas**; los archivos exactos
  de HEAD producen 185. El único diagnóstico desplazado es el mismo faltante de
  stubs de openpyxl.styles, antes en el export cashflow retirado y ahora en el
  primer import intacto de Tesorería. No se corrigió deuda de otros módulos.
- `pip check`: sin conflictos. Esto no demuestra compatibilidad de una API con
  un pin opcional del repositorio. Recibos: `types-receipt.json`,
  `mypy-scoped-pass.log`, `mypy-external-metadata.log`,
  `mypy-HEAD-baseline.log`, `mypy-final-consumers-routes.log`, `mypy-attribution.json`.

Tras la autorización explícita «autorizo» se instalaron únicamente MCP 1.29.0
y su dependencia faltante httpx-sse 0.4.3 en `/tmp/cashflow001-mcp129`.
No se sustituyeron paquetes de la `.venv` compartida ni dependencias productivas.
Los cambios adicionales de código fueron formato y tipos, cubiertos por la spec
aprobada y la nueva ejecución de las 172 pruebas, incluidos PG y navegador.
La instalación temporal posterior no modificó código: se comprobaron sus hashes.
Las deprecaciones de datetime/SQLAlchemy y el aviso de reescritura de pytest no
son fallos de aceptación. Los fallos de construcción detectados se corrigieron
y sus pruebas afectadas se repitieron; no quedan fallos funcionales conocidos.

El PostgreSQL temporal exclusivo de esta tarea se detuvo tras la aceptación.
Para repetir las cuatro pruebas de integración se necesita un clúster temporal
equivalente; no apuntar el DSN a producción. Sin él, los skips no acreditan PG.

## Limitaciones y desviaciones

No hay desviación material del alcance aprobado. La concreción del consumidor
T04 y las dos etiquetas está registrada en el manifiesto. No se introdujeron
owners, dependencias productivas, tablas, jobs, permisos ni reglas de clasificación nuevos.

- Apertura/cierre positivos, moneda histórica ausente, traspasos clasificados y
  atribución bancaria por proyecto requieren evidencia canónica. No se infieren.
- Los totales bancarios describen movimientos importados autorizados; no prueban
  que estén cargados todos los estados de todas las cuentas.
- CxC heredada sin cobertura completa acreditada y presupuesto sin moneda o
  distribución completa permanecen N/D. Se conserva el selector presupuestal
  legible y su selección definitiva; edición y año de cashflow son independientes.
- El riesgo CxP sin captura se identifica N/D con motivo. No se inventó otro owner
  para reconstruirlo; la navegación heredada se conserva sin crear un enlace
  global desde un contexto empresarial no acreditado para ese destino.
- Las cifras de la plantilla Excel y las fixtures nunca se usan como importes
  productivos. Las capturas son sintéticas, no UAT ni evidencia de sam.chat.

## Revisión y siguiente checkpoint

La revisión independiente de implementación no conserva hallazgos críticos ni
importantes de código; ver `validation.md`. El gate privado ya está aprobado.
Las limitaciones de tipado externo/deuda previa impiden declarar limpieza general
de tipos del repositorio, aunque el análisis local aprobado no presenta errores. Faltan además conciliación real,
UAT autenticada y aceptación de Finanzas. Este informe no autoriza publicación.

**Canon unchanged:** se aplican la separación de banco real/obligaciones/plan/
cobranza/proyección y la autoridad existentes, sin alterar reglas de escritura.
Siguiente checkpoint humano: revisión del diff y de estas limitaciones.

Evidencia: `acceptance.log`, `coverage.json`, `inventory-check.log`,
`private-plugin.log`, `integration-manifest.json` y el driver reproducible.
Capturas: `/tmp/cashflow001-browser/cashflow-1440.png` y `cashflow-390.png`.


## Cierre autorizado del SDK

La autorización «autorizo» permitió instalar MCP 1.29.0 y httpx-sse 0.4.3 sólo en
`/tmp/cashflow001-mcp129`. Su ruta de importación y requisitos se verificaron
antes del gate: **135 pruebas aprobadas**. No se adaptó el plugin a MCP 2.3.0 ni
se cambió el entorno compartido. El bloqueo de compatibilidad queda cerrado.
Persisten exclusivamente los límites de tipado global/fuentes y los checkpoints
humanos, conciliación productiva y UAT señalados en este informe.
