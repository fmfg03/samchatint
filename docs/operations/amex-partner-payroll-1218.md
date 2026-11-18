# Conciliación de cargos AMEX de socios con nómina de Plataforma 1218

El punto de entrada está en el detalle de un estado AMEX directo de
Plataforma Sports. Un cargo reconocido a la cuenta deudora del socio
muestra **Conciliar nómina 1218** para Finanzas con acceso a ambas empresas.
El saldo parte de la línea deudora del corte original e incluye el IVA.
El flujo acepta cargos con fecha igual o posterior al inicio autorizado.
Los cargos anteriores se revisan en su propio proceso de corrección.

## Evidencia y archivos COI

1. Adjuntar la póliza de COI de 1218 en PDF o XLSX de hasta 5 MiB.
   Capturar RFC, periodo, tipo, folio, renglón, fecha del descuento,
   importe individual y cuenta acreditada. Si hay un asiento agrupado,
   incluir el detalle individual en el mismo archivo y usar la referencia
   literal del renglón del descuento. Finanzas coteja esa evidencia contra
   COI y la cuenta deudora del socio. El archivo se conserva como evidencia
   y los datos se confirman en pantalla.
2. Revisar el importe a aplicar y adjuntar el mismo archivo al confirmar.
   SamChat limita el total aplicado al importe confirmado del renglón,
   incluso cuando se reparte entre varios cargos del socio. En Sports, la póliza reclasifica
   Deudores socios a Cuentas por cobrar a 1218. En 1218, si el descuento
   ya está en la cuenta por pagar a Sports, el recibo vincula esa póliza.
   Si está en la cuenta puente autorizada, el archivo COI reclasifica ese saldo.
3. Al transferir a Sports, seleccionar el cargo bancario de 1218 y el
   abono de Sports con la misma clave de rastreo. El flujo conserva las
   pólizas bancarias ya vinculadas que coinciden con ambos movimientos
   contables esperados. Cada póliza bancaria respalda una sola transferencia
   y permite repartirla entre cargos sin exceder su importe. Los movimientos
   restantes generan su archivo COI por empresa en periodos contables abiertos.

Cada archivo incluye la empresa, el cargo, el UUID de la confirmación
y la fecha contable. Verificar esa referencia al importar en COI. La
confirmación en SamChat acredita la conciliación y la generación del
archivo. La importación contable se verifica en la empresa correspondiente
en COI.

## Configuración por Finanzas y operación

El proceso requiere la migración explícita
`database/migrations/20261118_amex_partner_payroll.sql` y la variable del
servicio `AMEX_PARTNER_PAYROLL_1218_CONFIG`. El arranque excluye la tabla
nueva de la creación automática, junto con la tabla privada de evidencia
COI. El primer descuento nuevo sirve para la validación de Finanzas en COI.

| Clave | Valor a verificar |
| --- | --- |
| `sports_actor_ids`, `p1218_actor_ids` | IDs de empleados autorizados por cada empresa. La intersección también requiere el permiso vigente AMEX. |
| `starts_on` | Fecha de inicio autorizada en formato AAAA-MM-DD. El corte se aplica a la fecha del cargo original. |
| `sports_rfc`, `p1218_rfc` | RFC del contexto AMEX de Sports y de la empresa que emitió la póliza COI. |
| `partner_debtor_ids` | IDs de las cuentas deudoras autorizadas de los socios en Sports. |
| `sports_receivable`, `p1218_payable`, `p1218_withholding` | Cuentas activas como objetos con `id` y `code`. La última corresponde a la contrapartida de retención antes de reclasificar. |
| `sports_bank`, `p1218_bank` | Cuentas contables activas de banco, con `id` y `code`. |
| `sports_bank_number`, `p1218_bank_number` | Identidades de las cuentas bancarias MXN en las fuentes importadas. |

Si la póliza carece de una línea de descuento identificable, la aplicación
queda pendiente hasta que Finanzas coteje el detalle del socio y la cuenta
acreditada. La vista previa identifica el asiento por empresa y la descarga
autorizada recupera los bytes exactos del archivo confirmado.

Para retirar el acceso a nuevas operaciones, retirar la configuración
del servicio y conservar los recibos. El rollback SQL requiere las tablas
vacías. Con evidencia registrada, revertir el código conservando las
tablas y respaldos de auditoría.

Canon unchanged. La conciliación posterior usa el corte AMEX existente
y preserva su fuente, su póliza y el tratamiento de cargo completo a
Deudores socios.
