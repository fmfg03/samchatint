# Regularización de cinco calendarios base de Liga

Detalle de implementación de AC2, conservando la distribución efectiva aprobada de Liga en semana40. No cambia la especificación ni su hash. Este plan está preparado para revisión del coordinador; todavía no se ha aplicado.

El owner existente `replace_budget_line_monthly_plan(...ensure_schema=False)` sustituye los52 registros de plan y52 asignaciones por cada una de las cinco líneas fijadas en el JSON. Los260+260 UUID de calendario se renuevan por contrato del owner; se conservan íntegros en el JSON fuente fijado y en un audit presupuestal append-only dentro de la misma transacción, antes de la sustitución. Los históricos de revisiones semanales y sus snapshots siguen intactos.

Se preservan exactamente todos los ingresos esperados por semana y la distribución efectiva anterior. Sólo los cinco importes positivos ya autorizados pasan de cero en el calendario base a su semana40 ya vigente. El owner valida suma, editable reforecast y versión. Después se usa la aprobación normal, sin omitir controles, y se renuevan las revisiones semanales. Cualquier fuente distinta, ingreso diferente, UUID adicional o cambio económico revierte toda la operación.

JSON SHA256: `52a040cda18a9b8df7e2220a39677616229495f9d1eb8d0ac0c0edc22b9930c3`.

Fuera de este ajuste permanecen inmutables pagos, pólizas, conceptos, fases, importes, referencias y calendarios de otras líneas. La eliminación/reinserción sólo puede ejecutarse desde la llamada del owner para estos cinco UUID y estas dos tablas. Debe verificarse el diff exacto y la reversión real en la copia restaurada antes de cualquier aplicación productiva.
