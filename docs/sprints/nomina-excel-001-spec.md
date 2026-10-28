# NOMINA-EXCEL-001 — Especificación técnica propuesta

Fecha: 2026-10-28.
Estado: ESPECIFICACION_APROBADA para implementación local; no autoriza efectos productivos.
Recibo: usuario, conversación actual: «apobada», después de presentar esta especificación.
Historia: [nomina-excel-001-story.md](nomina-excel-001-story.md), aprobada en conversación.
Evidencia: revisión del árbol local; no se consultó la base de datos ni producción.

## 1. Superficie y decisión de diseño

Runtime: `copa_telmex_dashboard.py`; dominio: nómina de `src/devnous/gastos/`;
persistencia: SQLAlchemy/Postgres. Reutilizar sesión y `require_admin_finanzas()`.
El importador Runa es de fichas de empleados y espera la hoja `Empleados`:
no es el propietario adecuado para importar importes de esta hoja `N`.

Crear un servicio de carga de importes dentro del mismo dominio. Su propietario
será `payroll_amount_import_service.py`; reutilizará `PayrollEmployee`,
`PayrollEmployer`, `PayrollPeriod` y `CuentaContable` para resolver identidades.
La propuesta incorpora dos tablas de recepción y validación, detalladas abajo.
Este cambio de persistencia requiere aprobar esta especificación.

No insertar la carga en `PayrollRunLine`: el Excel carece del desglose
gravado/exento requerido por la prenómina calculada. La UI actual elige la última
ejecución del periodo y su generador contable agrupa percepciones por propósito;
publicar allí esta carga expondría datos incompletos al cálculo, exportación,
recibos y póliza existentes. La carga tendrá detalle propio dentro de Nómina,
conservando por empleado cada importe y cuenta, sin iniciar esos procesos.

## 2. Contrato del archivo

Ajustarse a la hoja `N`, encabezados de fila 5 y registros identificados por
CLAVE y nombre. Ignorar filas vacías, títulos y totales; informar filas con
identificación incompleta. No depender del número de empleados del ejemplo.
Normalizar espacios, saltos de línea y acentos de encabezados; rechazar columnas
obligatorias ausentes o duplicadas. Las letras siguientes documentan la muestra;
el parser resuelve por encabezado y verifica pares concepto/cuenta.

| Importe | Concepto | Cuenta de referencia |
| --- | --- | --- |
| H | Sueldo | G |
| J | Horas extras | I |
| K | Bono | Sin columna explícita |
| M | Ayuda internet y luz | L |
| N | Subsidio | Sin columna explícita |
| P | Vacaciones | O |
| R | Prima vacacional | Q |
| S | Compensación | Sin columna explícita |
| V | Préstamo personal | U; conservar por empleado |
| X | IMSS | W |
| Z | ISR | Y |
| AC | Neto | AB; sólo destino declarado, no prueba de pago |

Conservar CLAVE, nombre de origen, días pagados, salario diario y totales T/AA/AC.
Resolver empleados activos por `PayrollEmployee.employee_number`, nunca por
similitud de nombres. Una clave desconocida o repetida es error bloqueante.
Resolver cuentas activas por código exacto, preservando también su UUID;
no crear cuentas ni aplicar heurísticas o mappings globales que sustituyan
las cuentas por fila. Mostrar diferencias de nombre para revisión sin cambiar
la identidad resuelta por clave.

## 3. Validación de importes y fórmulas

Usar `Decimal`, rechazar valores no finitos, errores Excel y textos de cuenta
en columnas de importe. No convertir un vacío en cero silenciosamente.
No recalcular ISR, IMSS, subsidio, salario ni componentes fiscales.

Leer fórmulas y valores almacenados con openpyxl, dependencia ya existente.
No ejecutar macros, vínculos externos ni un evaluador genérico de fórmulas.
Un importe con fórmula sin resultado almacenado válido requiere un Excel
recalculado; no se deduce del nombre del concepto. Conservar la presencia de
fórmula y advertir que openpyxl no garantiza la vigencia de su resultado.

Validar sumas con los importes de conceptos explícitos: percepciones H/J/K/M/N/P/R/S,
deducciones V/X/Z y neto igual a percepciones menos deducciones. Esta convención
es aritmética del layout, no una clasificación fiscal del subsidio.
Comparar a centavos con T/AA/AC, mostrando diferencias por fila y en el conjunto;
no sustituir totales de origen ni confirmar un lote con discrepancias.

Hallazgo: T7 suma H:R y excluye compensación S, mientras T24 suma H:S.
No copiar esa inconsistencia al importador; una compensación con importe puede
producir una diferencia que requiere corregir la fuente antes de confirmar.

Conservar importes negativos en la vista previa como incidencia bloqueante:
su tratamiento requiere una regla aprobada de ajustes, ausente en esta historia.
El ejemplo con todos los montos en cero sirve para previsualizar la estructura;
proponer bloquear su confirmación por no contener importes efectivos.

Los conceptos no nulos sin cuenta quedan como pendientes visibles. No impiden
previsualizar ni guardar el lote validado aritméticamente, pero el lote queda
en estado `pending_mapping`; no se presenta como listo para contabilizar.

## 4. Flujo y autoridad

Rutas nuevas propuestas, integradas en el router actual de nómina:

- GET `/admin/nomina/carga-importes`: archivo, empleador y periodo existentes.
- POST `/admin/nomina/carga-importes/preview`: lectura y vista previa, sin escritura.
- POST `/admin/nomina/carga-importes/confirmar`: confirmación explícita del lote.
- GET `/admin/nomina/carga-importes/{import_id}`: recibo y detalle persistido.

Usar formulario multipart y HTML escapado según el patrón del módulo; sin
dependencias nuevas. Limitar archivo a 10 MiB, 5.000 empleados y 100 MiB de XML
descomprimido; rechazar XLSM, archivos corruptos y paquetes que superen límites.
Los límites son controles técnicos propuestos, no reglas salariales.

Cada petición exige identidad activa y el guard de Finanzas existente. Empleador
y periodo se seleccionan explícitamente; el texto del título no crea ni autoriza
un empleador. Todos los empleados deben pertenecer al empleador seleccionado
mediante su registro patronal; ausencia o incompatibilidad bloquea la confirmación.
No se agrega acceso por rol ni se activa multiempresa como parte de este cambio.
La pertenencia al empleador limita integridad; no constituye una autorización
de empresa adicional a la disponible en el runtime inspeccionado.

Revalidar permisos, empleador, periodo y cuentas al confirmar. Impedir cargas
en periodos cerrados o contabilizados. No crear periodos automáticamente.
Reenviar el archivo al confirmar y comparar un resumen firmado por el servidor,
ligado a actor, periodo, empleador, SHA-256, filas normalizadas, incidencias y
caducidad de 30 minutos. Un cambio de archivo o resolución exige otra vista previa.
Reutilizar firma y protección de formularios del runtime, sin exponer datos
personales en token legible, URL, logs o mensajes de excepción.

## 5. Persistencia, concurrencia y recibo

Migración explícita para dos tablas:

- `payroll_amount_imports`: UUID; FK a periodo, empleador y actor; SHA-256 del
  archivo y del contenido normalizado; versión del parser; estado `validated`
  o `pending_mapping`; totales; fecha; resumen de incidencias y metadatos de
  origen. Restricciones únicas de archivo y contenido por periodo/empleador.
- `payroll_amount_import_lines`: FK al lote y empleado; fila y clave de origen;
  días/salario diario y totales originales; JSONB de componentes con concepto,
  importe decimal como texto, código y UUID de cuenta, procedencia y pendientes.
  Unicidad de empleado y de fila dentro del lote.

Guardar lote y líneas en una transacción; bloquear el periodo al confirmar y
revalidar estado. La carga es completa: errores de empleados, cuentas, formato
o sumas impiden guardar todas las filas; los pendientes de mapeo se distinguen
de esos errores y se conservan explícitamente.

Un reintento idéntico devuelve el recibo existente sin duplicar. Un archivo
distinto con empleados que ya estén cargados en el mismo periodo/empleador
se rechaza como conflicto; no reemplaza una carga anterior. Serializar este
control con el bloqueo del periodo para cubrir peticiones simultáneas.
Registrar actor, fecha, huellas, cantidades y resultado, sin volcar el Excel
ni nombres en logs. El archivo original no se almacena en una carpeta pública;
el detalle y huellas persistidos permiten reconstruir el mapeo confirmado.

No modificar perfiles de empleado, mappings globales, `PayrollRun`, pólizas,
CFDI, Payment Run ni estados de pago. La cuenta AB no autoriza a generar una
salida bancaria. Generar póliza o pagar requieren su acción y alcance posteriores.

## 6. Archivos previstos y pruebas

Cambios de implementación propuestos:

- `src/devnous/gastos/services/payroll_amount_workbook_parser.py` (nuevo).
- `src/devnous/gastos/services/payroll_amount_import_service.py` (nuevo).
- `src/devnous/gastos/models.py` (dos modelos de recepción).
- `src/devnous/gastos/routes/user_routes.py` (cuatro rutas y acceso desde Nómina).
- `database/migrations/20261028_payroll_amount_imports.sql` (nueva).
- `tests/unit/gastos/test_payroll_amount_workbook_parser.py` (nuevo).
- `tests/unit/gastos/test_payroll_amount_import_routes.py` (nuevo).
- `tests/integration/test_payroll_amount_import_service.py` (nuevo).
- Inventario y matriz del plugin privado afectados por el fingerprint del router;
  regenerar mediante sus herramientas existentes, sin habilitar escrituras MCP.

Pruebas sintéticas sin datos personales reales: cuentas distintas entre empleados;
préstamos individuales; columnas reordenadas; 14 claves del ejemplo como verificación
local de lectura; pendientes de mapeo; discrepancia por compensación; fórmulas sin
caché; ceros/vacíos; archivo corrupto y límites; importes inválidos; claves/cuentas
desconocidas; permisos; otro empleador; periodo cerrado; token alterado/caducado;
reintento, conflicto y concurrencia transaccional; rollback de lote incompleto.
Comprobar ausencia de cambios en tablas de cálculos, pólizas y pagos.

Ejecutar pytest focalizado de parser, rutas, integración Postgres aislada y
regresiones de nómina, más lint/formato de archivos nuevos y diff hygiene.
Ejecutar también inventario/matriz del plugin privado con `PYTHONPATH=src`;
confirmar el comando exacto de su herramienta antes de regenerar.
Objetivo: ≥85% de cobertura del código nuevo/modificado. La revisión final será
de sólo lectura contra historia, especificación y reglas del repositorio.

## 7. Migración, reversión y límites de aceptación

La implementación prepara la migración; aplicarla en producción requiere
autorización separada, respaldo y verificación por el propietario. No DDL en
arranque. Reversión del código conserva tablas y evidencia; no borrar cargas
confirmadas como mecanismo de rollback. No dependencias nuevas ni jobs.

Riesgos pendientes: no se verificó que las claves/cuentas existan en producción;
falta un Excel con importes reales recalculados; bono/subsidio/compensación pueden
requerir cuentas; ninguna cuenta ni pertenencia empresarial se deduce del título.
La aceptación productiva requiere UAT autenticada con una carga real autorizada.

Canon unchanged: se añade recepción gobernada de un formato de nómina dentro del
dominio existente y se conserva la separación entre archivo, cálculo, póliza,
pago y aceptación. No se cambia el canon ni se afirma despliegue o aceptación.

## 8. Aclaración del usuario y hallazgos de implementación

El usuario aportó la clasificación conceptual después de aprobar esta
especificación: bono/bonificación y compensación a gastos de personal; subsidio
como menor gasto de nómina. No proporcionó códigos exactos de cuentas del
catálogo. Se conserva esta dirección para el mapeo contable posterior; esta
carga no netea importes ni genera asientos y mantiene los tres conceptos
pendientes de cuenta cuando tienen importe. No cambia el alcance aprobado.

La lectura completa de la plantilla detectó cuatro filas con nombre sin CLAVE,
además de las 14 claves distintas, y formato que se extiende a la última columna
de Excel. El lector inspecciona las filas XML reales para no confiar en dimensiones
declaradas incorrectas y limita la proyección a 64 columnas; las filas sin clave
y el archivo sin importes bloquean la confirmación. No se infieren identidades.

El HEAD base `d548d148f7521988587d6498b5461901c21a2ee3` no contiene
`scripts/private_plugin/inventory.py`, un inventario de rutas ni su suite/matriz.
No se incorporan desde otra rama: ese chequeo queda pendiente por falta del
artefacto/herramienta, no como evidencia de validación satisfactoria.
