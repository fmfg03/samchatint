# RQF-FIN-UAT-001 — Recorrido financiero integral

## Estado

`DRAFT_READY_FOR_FINANCE_REVIEW`

## Objetivo

Validar con Finanzas que los flujos financieros críticos de SamChat funcionen de punta a punta: captura, autorización, pago/cobro, evidencia, prepóliza contable, exportación Excel COI y tableros operativos.

La intención de esta story no es agregar features durante la sesión, sino separar claramente:

- defectos bloqueantes;
- ajustes menores de operación;
- cambios solicitados fuera del alcance validado;
- observaciones no bloqueantes.

## Regla de aceptación general

Un flujo queda aceptado sólo si Finanzas puede ver y/o descargar evidencia suficiente para reconstruir:

1. quién solicitó;
2. quién autorizó;
3. qué documento originó el movimiento;
4. qué pago/cobro ocurrió;
5. qué prepóliza se generó;
6. qué Excel puede cargarse o revisarse para COI;
7. qué referencia de Operaciones y referencia interna de SamChat amarran el caso.

## Matriz de resultado

| Resultado | Tratamiento |
| --- | --- |
| PASS | Flujo aceptado para línea base. |
| PASS con observación | No bloquea; queda documentado. |
| FAIL bloqueante | Se corrige antes de aceptar el módulo. |
| Cambio solicitado | Se registra como nueva story, no como defecto. |
| Fuera de alcance | Se documenta explícitamente. |

## Evidencia mínima por caso

Cada caso probado debe registrar:

- usuario que ejecuta;
- rol;
- fecha/hora;
- referencia Operaciones;
- referencia SamChat;
- capturas o archivos descargados;
- póliza/prepóliza esperada;
- póliza/prepóliza generada;
- resultado;
- observaciones.

---

# 1. Check de prepólizas contables

## 1.1 Solicitud pago a proveedores → Pago a proveedores

### Objetivo

Validar que una solicitud de transferencia a proveedor real genere el asiento/prepóliza esperada y sea descargable en Excel COI.

### Pasos

1. Crear o seleccionar solicitud a proveedor.
2. Enviar a autorización.
3. Aprobar según matriz vigente.
4. Confirmar pago.
5. Revisar documento final.
6. Descargar prepóliza Excel COI.

### Evidencia esperada

- Documento de solicitud.
- Estado final pagado.
- Testigo de pago si aplica.
- Prepóliza con referencia Operaciones y referencia SamChat.
- Excel descargable.

### Resultado

`PENDING_FINANCE_UAT`

## 1.2 Solicitud de anticipo → Pago a beneficiario registrado

### Objetivo

Validar anticipos para beneficiario registrado, incluyendo terceros permitidos y operadores regionales si aplica.

### Pasos

1. Crear anticipo.
2. Seleccionar beneficiario/cuenta.
3. Enviar a autorización.
4. Aprobar.
5. Confirmar pago.
6. Revisar prepóliza y auxiliar.

### Evidencia esperada

- Solicitante distinto de beneficiario cuando aplique.
- Aprobador del solicitante, no del beneficiario.
- Cuenta bancaria del beneficiario.
- Prepóliza de anticipo.

### Resultado

`PENDING_FINANCE_UAT`

## 1.3 Informe de gastos vs Anticipo → Reembolso / comprobación

### Objetivo

Validar que el informe contra anticipo afecte correctamente comprobación, saldo, reembolso o devolución.

### Pasos

1. Seleccionar informe ligado a anticipo.
2. Revisar gastos capturados.
3. Cerrar informe.
4. Aprobar.
5. Revisar saldo.
6. Registrar reembolso/devolución si aplica.
7. Descargar prepóliza.

### Evidencia esperada

- Auxiliar de deudores correcto.
- Saldo cuadrado.
- Prepóliza de comprobación.
- Prepóliza de reembolso/devolución si aplica.

### Resultado

`PENDING_FINANCE_UAT`

## 1.4 Informe de gastos vs Reembolso directo

### Objetivo

Validar informe sin anticipo previo, con reembolso directo al empleado/beneficiario.

### Resultado

`PENDING_FINANCE_UAT`

## 1.5 Informe de gastos vs AMEX → Pagos AMEX

### Objetivo

Validar que los gastos pagados con AMEX empresa no generen saldo a favor del empleado y se reflejen como AMEX.

### Criterios específicos

- El informe debe marcar AMEX visualmente.
- La tarjeta AMEX debe afectar “Pagado por AMEX empresa”.
- No debe pedir cuenta bancaria de depósito cuando no hay reembolso al empleado.
- Propinas en consumo/alimentos deben capturarse como No Deducible.

### Resultado

`PENDING_FINANCE_UAT`

## 1.6 Tableros y exportación COI

### Objetivo

Validar que los tableros permitan llegar rápido a prepólizas y descargas Excel para COI.

### Resultado

`PENDING_FINANCE_UAT`

---

# 2. Módulo de Conciliación AMEX DG

## Objetivo

Validar flujo completo de AMEX DG: cargos, comprobación, propinas, CFDI, prepólizas y conciliación.

## Casos

| Caso | Resultado |
| --- | --- |
| Carga / consulta de cargos AMEX | PENDING_FINANCE_UAT |
| Comprobación de consumo | PENDING_FINANCE_UAT |
| Propina como No Deducible | PENDING_FINANCE_UAT |
| Prepóliza de pago AMEX | PENDING_FINANCE_UAT |
| Prepóliza de comprobación de consumo | PENDING_FINANCE_UAT |

---

# 3. Programación de pagos / Payment run

## Objetivo

Validar que Finanzas pueda programar pagos, operar cortes y confirmar pagos con testigo.

## Casos

| Caso | Resultado |
| --- | --- |
| Tablero de programación de pagos | PENDING_FINANCE_UAT |
| Corte operativo | PENDING_FINANCE_UAT |
| Selección de pagos | PENDING_FINANCE_UAT |
| Confirmación de pagos | PENDING_FINANCE_UAT |
| Testigo de pago | PENDING_FINANCE_UAT |
| Estado terminal sin revivir flujo | PENDING_FINANCE_UAT |

---

# 4. Módulo de Cuentas por Cobrar

## Objetivo

Validar flujo completo de CxC: programación, factura emitida, torneo/partida, prepóliza CxC, cobranza y prepóliza de cobro.

## 4.1 Programación de cobranza por facturar

Resultado: `PENDING_FINANCE_UAT`

## 4.2 Conexión factura emitida vs torneo y partida presupuestal

### Prepóliza esperada de CxC

- Debe: `1150-*` Cuentas por cobrar.
- Haber: `4100-*` ingreso presupuestal.
- Haber: `2140-001-001` IVA trasladado, si el CFDI trae IVA.

Resultado: `PENDING_FINANCE_UAT`

## 4.3 Confirmación de cobranza

### Prepóliza esperada de cobro

- Debe: `1120-001-001` bancos.
- Haber: `1150-*` cuentas por cobrar.

Resultado: `PENDING_FINANCE_UAT`

---

# 5. Descarga masiva SAT

## Objetivo

Validar que el módulo SAT alimente conciliación fiscal/operativa.

## Casos

| Caso | Resultado |
| --- | --- |
| Descarga automática programada | PENDING_FINANCE_UAT |
| Descarga manual | PENDING_FINANCE_UAT |
| Separación Emitidos / Recibidos | PENDING_FINANCE_UAT |
| CFDI SAT vinculados con ingresos/gastos SamChat | PENDING_FINANCE_UAT |
| CFDI pendientes de vincular | PENDING_FINANCE_UAT |
| Protocolo para asignar flujo/proyecto/partida a CFDI no conciliado | PENDING_FINANCE_UAT |

---

# 6. Conciliación bancaria

## Objetivo

Validar bancos contra flujos reales de SamChat.

## Casos

| Caso | Resultado |
| --- | --- |
| Presentación del flujo completo | PENDING_FINANCE_UAT |
| Bancos vs solicitudes/pagos | PENDING_FINANCE_UAT |
| Bancos vs CxC/cobros | PENDING_FINANCE_UAT |
| Bancos vs AMEX | PENDING_FINANCE_UAT |
| Partidas no conciliadas | PENDING_FINANCE_UAT |
| Protocolo para asignar flujo SamChat a partidas no conciliadas | PENDING_FINANCE_UAT |

---

# 7. Masivos y operación ágil

## Objetivo

Confirmar que el proceso sea empático y rápido para Finanzas.

## Casos

| Caso | Resultado |
| --- | --- |
| Descarga masiva de formatos Excel de Solicitudes | PENDING_FINANCE_UAT |
| Descarga masiva de Informes de Gastos | PENDING_FINANCE_UAT |
| Impresión directa desde SamChat | PENDING_FINANCE_UAT |
| Impresión masiva | PENDING_FINANCE_UAT |
| Descarga masiva de prepólizas Excel para COI | PENDING_FINANCE_UAT |

---

# Cierre esperado

La story cierra cuando exista:

- checklist revisado con Finanzas;
- evidencia de cada flujo crítico;
- lista de defectos bloqueantes;
- lista de cambios solicitados;
- lista de observaciones no bloqueantes;
- decisión explícita: `ACCEPTED`, `ACCEPTED_WITH_OBSERVATIONS` o `BLOCKED_BY_FINANCE_UAT`.

## Estado final

`PENDING_FINANCE_UAT`
