# NOMINA-EXCEL-001 — Entrega local y validación

Fecha: 2026-10-28.
Estado: IMPLEMENTADO_LOCAL; VALIDACION_LOCAL_COMPLETA; promoción autorizada.
Recibos: diff aprobado y «aprobado hasta deploy» en conversación.
Worktree: `/root/samchat/.worktrees/nomina-excel-001`.
Rama local: `feature/nomina-excel-001`.
Base: `d548d148f7521988587d6498b5461901c21a2ee3`.
Historia y especificación aprobadas en conversación. Sin commit, push, PR,
migración productiva, despliegue ni aceptación de negocio.

## Resultado y archivos

La carga permite revisar un XLSX, resolver empleados por clave y cuentas por
código, confirmar el lote completo con trazabilidad y consultar su recibo.
Conserva las cuentas e importes por empleado; no modifica cálculos fiscales,
perfiles, pólizas o pagos. La confirmación usa CSRF y un resumen firmado ligado
al actor, empleador, periodo y plan, con caducidad de 30 minutos.

Archivos de implementación:

- `src/devnous/gastos/services/payroll_amount_workbook_parser.py`: lector acotado,
  sumas y errores; no evalúa fórmulas ni inventa importes.
- `src/devnous/gastos/services/payroll_amount_import_service.py`: resolución,
  confirmación, control transaccional y reintentos.
- `src/devnous/gastos/models.py`: dos modelos de recepción.
- `src/devnous/gastos/routes/user_routes.py`: navegación, formulario, vista previa,
  confirmación y recibo bajo el guard de Finanzas existente.
- `database/migrations/20261028_payroll_amount_imports.sql`: migración preparada;
  ejecutada dos veces por fixture únicamente en esquemas Postgres temporales.

Pruebas añadidas:

- `tests/unit/gastos/test_payroll_amount_workbook_parser.py`.
- `tests/unit/gastos/test_payroll_amount_import_routes.py`.
- `tests/integration/test_payroll_amount_import_service.py`.

Documentación: historia, especificación y este informe. Evidencia en
`tests/artifacts/nomina-excel-001/`: `test-results.txt`,
`changed-code-coverage.json` e `implementation.diff`. El diff contiene código,
pruebas y los dos objetos aprobados; los recibos de ejecución se consultan aparte.
El checkout principal y sus cambios ajenos a esta tarea se preservaron.

## Criterios y comandos ejecutados

| Criterio aprobado | Evidencia |
| --- | --- |
| Montos y cuentas por fila | Prueba Postgres con dos empleados, sueldos y cuentas de préstamo distintos |
| Vista previa y diferencias | Pruebas de HTML escapado, sumas, compensación omitida, negativos y fórmulas sin caché |
| Correspondencias y duplicados | Empleado desconocido/inactivo, otro empleador, cuentas inactivas/ausentes, periodo cerrado; reintentos, conflicto y concurrencia |
| Tres conceptos pendientes | Bono no nulo se conserva con `pending_mapping`; no se infiere cuenta |
| Carga separada de póliza/pago | Importación funciona sin tablas de ejecuciones calculadas ni pólizas; vista previa no escribe |

Se ejecutó con Python 3.12 del venv existente, `PYTHONPATH=src` y
`EXPENSE_REPORT_RECEIPT_BLOB_COLUMN=archivo_data`. Postgres 16 temporal en
`/tmp/samchat-nomina-excel-001-pg`, sólo por socket local; sin conexión a producción.

Comando principal (variable de base apunta exclusivamente a esa instancia):

```bash
python -m pytest -q \
  tests/unit/gastos/test_payroll_amount_workbook_parser.py \
  tests/unit/gastos/test_payroll_amount_import_routes.py \
  tests/integration/test_payroll_amount_import_service.py \
  tests/unit/test_payroll_cfdi_inbox_contract.py \
  --cov=devnous.gastos.services.payroll_amount_workbook_parser \
  --cov=devnous.gastos.services.payroll_amount_import_service \
  --cov=devnous.gastos.routes.user_routes \
  --cov=devnous.gastos.models \
  --cov-report=json:/tmp/nomina-excel-001-coverage.json --cov-fail-under=0
```

Resultado: **66 aprobadas, sin fallos ni omisiones en esta suite**. La cobertura
global del router heredado no representa la cobertura del cambio: se intersectaron
las líneas ejecutables de coverage JSON con el diff y los módulos nuevos y se
exigió ≥85%. Resultado: **361/371 = 97.3%** del código nuevo/modificado;
parser 95.45%, servicio 97.59%, modelos y bloque de rutas añadidos 100%.
El reporte JSON identifica las líneas no cubiertas; no se afirma cobertura total.

Otros chequeos: `flake8 --max-line-length=88` en los cinco archivos nuevos de
Python y en los bloques añadidos a modelos/rutas: sin hallazgos; Black e isort
de los archivos nuevos: PASS; `git diff --check`: PASS. Se usaron herramientas
ya instaladas/en caché, sin instalar dependencias. Se formatearon únicamente las
clases y funciones nuevas de los archivos heredados.

Lectura local de la fuente original: 14 claves distintas y cuatro filas con
nombre sin CLAVE (21–24); montos almacenados en cero y salario diario vacío.
El lector muestra esas incidencias y bloquea su confirmación. La plantilla tiene
formato hasta la columna final de Excel; se limita la proyección a 64 columnas
y se inspeccionan las filas reales para no omitir datos por dimensiones erróneas.

## Revisión de sólo lectura y correcciones

El validador independiente revisó historia, especificación, código, SQL, pruebas,
resultados e integridad de los tres canons. Su hallazgo importante fue la posible
omisión silenciosa de filas con dimensiones XLSX subdeclaradas. Se corrigió
mediante inspección XML y `reset_dimensions`, con regresión de dos empleados.
La revalidación independiente reprodujo esa regresión y la de negativos: PASS.

También se conservan importes negativos en el detalle como errores bloqueantes
y se retiró una restricción de vigencia patronal que no estaba aprobada.
La observación documental sobre estado pendiente quedó corregida/verificada en
la línea 4 de la historia. No quedan hallazgos bloqueantes de código identificados.

## Chequeos pendientes, atribución y riesgos

- **Inventario/matriz del plugin privado: RESUELTO tras actualizar main (9 PASS).**
  Registro histórico de la primera base local: No existen la herramienta,
  los artefactos ni su suite en el HEAD base; clasificación: capacidad/artefacto
  preexistente ausente. No se importaron desde otra rama. El pendiente se cerró
  al integrar la base actual y regenerar sus dos artefactos.
- Black del router entero falla también contra el archivo original de HEAD,
  en una sección heredada de f-strings (línea 30897 del original). Clasificación:
  limitación preexistente del formateador. El bloque nuevo se formateó aislado
  y su lint pasó; no se reformateó ni reparó código ajeno.
- Los avisos SQLAlchemy/de `datetime.utcnow` provienen del código base; no son
  fallos de la suite ni se ampliaron cambios para corregirlos.
- No se ejecutaron la suite global, mypy global, build/deploy ni UAT productiva.
  El alcance de verificación es la suite focalizada indicada; UAT exige Excel
  real con importes y usuarios autorizados. La instancia temporal se detuvo.
- Faltan códigos de catálogo para bono, subsidio y compensación. El usuario indicó
  su clasificación conceptual: bono/compensación a gastos de personal y subsidio
  como menor gasto. Esa dirección está documentada, pero no se netean importes
  ni se generan asientos en la carga.
- No se verificó la existencia de las claves/cuentas contra datos productivos.
  La migración productiva requiere respaldo, autorización y verificación propios.

No hay desviaciones funcionales pendientes respecto del alcance aprobado.
El inventario inicialmente no disponible quedó verificado al actualizar main;
la evidencia local no sustituye la aceptación de negocio.

Canon unchanged: recepción gobernada dentro del dominio de nómina, conservando
fuente, cálculo, póliza, pago y aceptación como etapas separadas. No se editaron
los tres canons ni sus hashes.

## Promoción autorizada y reconciliación con main

El usuario aprobó el diff local y después autorizó «aprobado hasta deploy»: commit,
push, PR, merge, migración aditiva necesaria y despliegue, con UAT separada.
La base local antigua se reconcilió con `origin/main` en `7344b8a5d`; el único
conflicto fue el import existente de `UniqueConstraint`, preservando los otros imports.
El inventario privado sí existe en esa base actual: se regeneraron inventario y
matriz y sus nueve pruebas pasaron, cerrando el pendiente de verificación local.
La suite focalizada sobre main y con Python del runtime repitió 66 PASS.
Para CI se usa también la variable explícita de base de prueba ya configurada
`TEST_AMEX_DATABASE_URL`; nunca hay fallback a una base operativa. El escaneo
XML reutiliza el parser de openpyxl, igual que el lector del workbook.

La promoción productiva se prepara sobre la release activa `ab29ccc64`, con
únicamente el cambio de nómina y su inventario. Esto conserva las demás
diferencias financieras de main fuera de la release autorizada. El SHA del
candidato, respaldo, migración y verificación se registran en el recibo de release.

## Correcciones verificadas de la revisión del PR #511

La revisión automática detectó tres defectos dentro del contrato aprobado. Se
reprodujeron primero mediante tres pruebas fallidas y después se corrigieron:

- Las dos tablas nuevas se excluyen de `runtime_managed_tables` mediante el
  propietario existente `src/devnous/gastos/amex_schema_policy.py`. Este archivo
  adicional es necesario para cumplir la migración exclusiva del propietario;
  no se cambia ninguna otra tabla, permiso o regla de negocio.
- Una fila sin clave ni nombre que conserve importes o una fórmula sin resultado
  genera error bloqueante. Sólo se omiten filas realmente vacías/cero y rótulos
  explícitos de totales.
- El salario diario realmente vacío sigue permitido; una fórmula sin caché es
  error y no se convierte en un vacío. Se conserva su presencia en la fuente.

Resultado local posterior: **93 pruebas aprobadas**, incluyendo las regresiones
de los tres hallazgos y la suite existente del límite de DDL de AMEX. Los checks
del PR se repiten sobre el nuevo head; los resultados del head anterior no se
usan para autorizar la promoción. El primer candidato preparado no se desplegó.

CI completo detectó además una incompatibilidad del fixture HTTP con FastAPI
0.162: su grafo incluye nodos `_IncludedRouter` sin atributo `path`. El fixture
selecciona ahora sólo entradas con `path`; no cambia código productivo. Los
14 errores de setup quedaron atribuidos a esa prueba nueva y se corrigieron,
sin cambiar la baseline de fallos aceptados ni ocultar el fallo.
