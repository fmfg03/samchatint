# AMEX Sin CFDI — validación independiente

2026-10-21. Revisión de sólo lectura de implementación contra historia, especificación y límites aprobados; sin cambios productivos. Bootstrap de los tres canons en orden y hashes comprobados contra registro. Párrafos aprobados exactos de §10 presentes.

## Primera revisión — retorno a construcción

Critical: ninguno. Important: **I1 abierto**, revisión final no aprobada aún. Minor: ninguno pendiente.

I1: el guard de snapshot Sin CFDI verificaba longitud2 y existencia de cada movimiento en el ledger, sin garantizar multiplicidad. El validador alteró ambos snapshots del miembro para congelar dos débitos, omitiendo el crédito, y dejó el encabezado de movimientos con el par correcto. La confirmación y COMMIT se completaron. La exportación se negó con `ValueError: El corte no contiene un desglose completo de su póliza.` Quedó evidencia reconocida e inmutable que no podía exportarse.

Reproducción real con todos los guards activos: 1 caso, 4.36s. Harness `/tmp/test_amex_no_cfdi_validator_duplicate.py`; log `/tmp/amex-no-cfdi-validator-duplicate-20261021.log`. Sólo esta variante fue ejecutada por el validador. La duplicación de una partida en el encabezado y multiplicidad de líneas raíz fueron deducciones estáticas relacionadas.

Corrección requerida dentro del alcance aprobado: para `new_no_cfdi`, un miembro único en el encabezado y equivalencia exacta con multiplicidad entre sus dos movimientos congelados, los movimientos raíz correspondientes y las dos filas reales del ledger. Agregar regresiones de débito/crédito duplicado y encabezado duplicado; actualizar hash exacto de guard. No relajar ni reescribir contratos fiscales, prior o settlement.

El coordinador devolvió I1 al propietario de esquema; pendiente nueva verificación independiente. No se requiere cambio de historia, reglas o autoridad: se cumple el contrato ya aprobado de snapshot igual a ledger.

## Resto de la revisión

La inspección no identificó otras desviaciones del alcance: hashes históricos condicionales, decisiones auditadas explícitas, autoridad existente, importe bancario completo como gasto/pasivo, ausencia de identidad fiscal o impuestos inventados, migración owner-run y exclusión de DDL de startup. Se reconocen las pruebas de builders y aceptación independiente sin sustituir la reproducción propia del validador.

Los fallos heredados de estilo/tipos están documentados con reproducción sobre baseline; no se afirma que todo el repositorio pase esas comprobaciones. La corrección de `.coverage` y del estado del reporte fue documental, sin cambios funcionales.

Publicación, migración, despliegue y UAT con Contabilidad siguen separados. Ningún archivo de implementación fue modificado por el validador.

## Revalidación final — I1 cerrado

Resultado independiente: **Critical: ninguno. Important: ninguno pendiente. Minor: ninguno.** I1 quedó cerrado dentro del alcance aprobado.

La corrección compara multiconjuntos exactos: encabezado de partidas contra miembros SQL canónicos; movimientos raíz contra los movimientos de esos miembros; y, para Sin CFDI, sus dos movimientos contra las filas reales de ledger incluyendo concepto. Los controles globales nuevos se activan sólo en cortes que contienen `new_no_cfdi`; no cambian las reglas fiscales ni las reglas de prior/settlement.

El validador conservó el harness histórico y ejecutó una copia que ahora exige rechazo: **1 PASS, 4.34s**. Se comprobaron cero cortes, miembros, pólizas, líneas, consumos y representaciones tras la operación fallida. Además ejecutó independientemente **8/8 variantes**, 16.85s, 233 advertencias heredadas: débito/crédito duplicado, partida duplicada, movimientos raíz extra/faltantes/huérfanos, encabezado huérfano y concepto alterado.

Evidencia: `/tmp/amex-no-cfdi-validator-i1-closure-20261021.log` y `/tmp/amex-no-cfdi-validator-i1-variants-20261021.log`. Los nueve hashes de cuerpos de función coinciden exactamente con el contrato; canon/registro y diff-check aprobados.

SQL SHA256 `4fa8d42850e05db21c4b370771114f063f79c38f808564a44b5c6ab1c3e4ca7e`. Contrato SHA256 `d48e131e75e5f9febf58c6245d3d27a2849222f78beaa40525b9f2d25e08b1e5`.

Sin archivos de producto modificados por el validador ni datos productivos consultados/escritos. Lease PostgreSQL liberado. Los fallos heredados de estilo/tipos siguen declarados; esta revisión no equivale a aprobación de negocio, publicación o despliegue. La corrección de I1 cumple la especificación ya aprobada y no introduce desviaciones de reglas, autoridad o arquitectura.
