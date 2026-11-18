# Conciliación de cargos AMEX de socios con nómina de Plataforma 1218

El punto de entrada está en el detalle de un estado AMEX directo de
Plataforma Sports. Un cargo reconocido a la cuenta deudora del socio
muestra **Conciliar nómina 1218** para Finanzas con acceso a ambas empresas.
El saldo parte de la línea deudora del corte original e incluye el IVA.

## Evidencia y archivos COI

1. Seleccionar la partida de una nómina cerrada de 1218 y el concepto
   configurado. Indicar la referencia del comprobante que acredita el
   descuento aplicado y la línea de retención en su póliza. El cierre
   del periodo por sí solo acredita el estado de la corrida.
2. Revisar y confirmar el importe. En Sports, la póliza reclasifica
   Deudores socios a Cuentas por cobrar a 1218. En 1218, si el descuento
   ya está en la cuenta por pagar a Sports, el recibo vincula esa póliza.
   Si está en la cuenta puente autorizada, el archivo COI reclasifica
   ese saldo. El generador nunca rebaja el neto de nómina.
3. Al transferir a Sports, seleccionar el cargo bancario de 1218 y el
   abono de Sports con la misma clave de rastreo. El flujo conserva las
   pólizas bancarias ya vinculadas que coinciden con ambos movimientos
   contables esperados. Los movimientos restantes generan su archivo
   COI por empresa.

Cada archivo incluye la empresa, el cargo, el UUID de la confirmación
y la fecha contable. Verificar esa referencia al importar en COI. La
confirmación en SamChat acredita la conciliación y la generación del
archivo. La importación contable se verifica en la empresa correspondiente
en COI.

## Configuración por Finanzas y operación

El proceso requiere la migración explícita
`database/migrations/20261118_amex_partner_payroll.sql` y la variable del
servicio `AMEX_PARTNER_PAYROLL_1218_CONFIG`. El arranque excluye la tabla
nueva de la creación automática.

| Clave | Valor a verificar |
| --- | --- |
| `sports_actor_ids`, `p1218_actor_ids` | IDs de empleados autorizados por cada empresa. La intersección también requiere el permiso vigente AMEX. |
| `sports_rfc`, `p1218_rfc`, `employer_id` | RFC del contexto AMEX de Sports, RFC de 1218 e ID de su empleador de nómina. |
| `partners` | Mapa de ID de cuenta deudora del socio en Sports a ID de la persona en nómina de 1218. |
| `deduction_concept` | Clave que identifica específicamente el descuento AMEX en la partida de nómina. |
| `sports_receivable`, `p1218_payable`, `p1218_withholding` | Cuentas activas como objetos con `id` y `code`. La última corresponde a la contrapartida de retención antes de reclasificar. |
| `sports_bank`, `p1218_bank` | Cuentas contables activas de banco, con `id` y `code`. |
| `sports_bank_number`, `p1218_bank_number` | Identidades de las cuentas bancarias MXN en las fuentes importadas. |
| `p1218_payroll_journals` | Opcional. Mapa ID de corrida a ID de póliza 1218 importada de COI, acreditada por Finanzas si la póliza no lleva el vínculo nativo `payroll_run:<id>`. |

Si la póliza de nómina carece de una línea de retención identificable,
Contabilidad debe acreditar la contrapartida de origen antes de aplicar el
descuento al saldo AMEX. Un importe neto o la fecha del periodo por sí solos
no identifican esa cuenta.

Para retirar el acceso a nuevas operaciones, retirar la configuración
del servicio y conservar los recibos. El rollback SQL requiere la tabla
vacía. Con evidencia registrada, revertir el código conservando las
tablas y respaldos de auditoría.

Canon unchanged. La conciliación posterior usa el corte AMEX existente
y preserva su fuente, su póliza y el tratamiento de cargo completo a
Deudores socios.
