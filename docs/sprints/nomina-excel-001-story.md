# NOMINA-EXCEL-001 — Historia aprobada

Fecha: 2026-10-28.
Estado: HISTORIA_APROBADA; especificación aprobada para implementación local.
Recibo: usuario, conversación actual: «aprobada», después de presentar esta historia.

Como responsable de nómina, quiero cargar el Excel con importes por empleado y
concepto, revisar su distribución contable y confirmar la importación.

## Criterios de aceptación aprobados

1. Respetar montos y cuentas de cada fila.
2. Mostrar una vista previa con percepciones, deducciones y neto; señalar diferencias.
3. Identificar empleados o cuentas sin correspondencia y evitar cargas duplicadas.
4. Dejar bono, subsidio y compensación pendientes de mapeo cuando tengan importe,
   sin inventar cuentas.
5. Mantener la carga separada de la generación de póliza y del pago.

## Fuente e interpretación

Archivo: `Copia de ARCHIVO DE NOMINA CON CUENTAS CONTABLES.xlsx`.
SHA-256: `deed39853ba5fd1938a8168ea9a15a93715a5bed1d02e32df8a24ff80400a6a9`.
El usuario aclaró que los datos a cargar son montos dirigidos a las cuentas de
referencia. Las cuentas se conservan como destinos; no se convierten en importes.

## Límites

No crear empleados o cuentas por inferencia; no recalcular impuestos, timbrar,
marcar como pagada la nómina, sustituir datos existentes ni aplicar migraciones
de producción como efecto de esta historia. La aceptación con un archivo con
importes reales sigue pendiente. El archivo de muestra tiene 14 claves distintas
de empleado, cuatro filas adicionales con nombre sin CLAVE, importes visibles en
cero y salario diario vacío. La confirmación requiere corregir esas incidencias.

Canon unchanged: esta historia documenta una carga gobernada de nómina dentro del
dominio existente; no cambia autoridad, estados financieros ni principios canónicos.
