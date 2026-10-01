# AMEX: revisión por partida y póliza por corte de un informe

Fecha: 2026-10-01. Estado: ajuste técnico aprobado por el usuario.

## Decisiones confirmadas

La empresa debe el pago a AMEX en todas las tarjetas. El gasto se reconoce una sola vez. En el punto 9, Finanzas revisa cada partida y puede elegir gasto o cuenta de deudores socios 1170-002-XXX. La modalidad socio carga el total de la partida aunque esté facturada y clasificada automáticamente. Se permite reclasificación autorizada si ya existe póliza, conservando la original. Cada corte pertenece a un solo informe y genera una sola póliza, nunca una por partida.

## Ajuste a la implementación aprobada

El vínculo de identidad entre informe y cargo importado sigue siendo obligatorio; no crea una póliza. La selección gasto/socio y el check de revisión pertenecen a Contabilidad, en el punto 9, no a la pantalla de conciliación automática.

### Punto 10: conciliación automática

Importar, vincular evidencia y preparar la clasificación automática. Esta clasificación es una propuesta revisable, no prevalece sobre una decisión manual de Finanzas. Para consumos incluidos en este flujo del punto 9, la conciliación no genera otro cargo al gasto: el registro final se consolida en el corte contable. Cuando exista una póliza previa identificable, se reutiliza su evidencia y cualquier cambio se trata como reclasificación, sin duplicar el reconocimiento.

### Punto 9: revisión y corte

1. Un informe aprobado habilita sus partidas para revisión contable; la aprobación operativa no sustituye el check de Finanzas.
2. Finanzas revisa cada partida, confirma gasto o deudor socio y, para este último, elige cuenta activa 1170-002-XXX y captura motivo. La identidad de la cuenta es explícita; no se infiere por el titular.
3. El corte corresponde al informe completo. Requiere todas sus partidas activas revisadas y el mismo mes contable; no permite omitir partidas que aún estén pendientes. Este límite conserva el canon vigente de una póliza completa por informe.
4. Al confirmar el corte, una transacción bloquea el informe y sus consumos, valida estados, importes, cuentas y versiones de revisión, y genera una sola cabecera con todas las líneas correspondientes. Cada línea conserva su partida, consumo, tarjeta, evidencia y decisión.
5. Partida gasto: conservar desglose fiscal aplicable y abonar al pasivo de su tarjeta. Partida socio: cargar el total al deudor elegido y abonar el mismo total al pasivo; el CFDI queda como evidencia y no genera otro cargo a IVA/gasto/no deducibles para esa partida.
6. Si un consumo ya está contabilizado, no volver a reconocer su gasto. Un cambio gasto→socio genera los movimientos compensatorios exactos de su desglose anterior y el cargo completo al deudor, manteniendo el saldo de AMEX. Las reclasificaciones del corte se agrupan en una sola póliza de ajuste, no una por partida. La póliza previa permanece intacta y relacionada con el corte de ajuste.
7. Un informe ya cerrado no puede generar otro reconocimiento inicial. Reintentos retornan el mismo corte/póliza; una reclasificación posterior requiere un corte de ajuste explícito, autorizado y con motivo. La operación de ajuste no vuelve a cobrar ni registrar pago AMEX.

## Persistencia y exportación

- Registro de revisión por partida con tratamiento, cuenta socio, actor, motivo y versión; no sobrescribir automáticamente una decisión manual.
- Registro de corte por informe, tipo inicial/ajuste, selección completa, versión/clave idempotente, actor, período, fecha y póliza única. Relacionar todas las partidas y recibos de consumo incluidos.
- Restricciones únicas y bloqueo transaccional para impedir dos cortes iniciales o dos ajustes del mismo estado. Cabecera, movimientos, recibos y cambios de estado se guardan juntos; cualquier fallo revierte el corte completo.
- El exportador COI toma las decisiones congeladas en el corte y emite una sola cabecera/FIN_PARTIDAS. Descargar de nuevo no crea otra póliza ni reclasificación. No recalcular desde una clasificación automática mutable.
- Mantener el pasivo por tarjeta y Santander para el pago. Conservar fecha económica y bloqueo de períodos cerrados; la fecha de ajuste debe ser explícita y autorizada.
- Sin backfill automático: una póliza histórica sin identidad y desglose verificable queda bloqueada para revisión separada.

## Alcance de archivos y pruebas

Completar modelos/migración de reconocimiento compartido y añadir revisión/corte; servicios AMEX y servicio de corte; hook de aprobación sólo donde deba preparar la revisión; pantalla y acciones de Contabilidad para checks, tratamiento y confirmación del corte; exportación COI del corte; historia/spec AMEX y pruebas correspondientes. Conservar el aislamiento respecto del PR #444.

Pruebas: varias partidas de un informe producen una sola póliza; mezcla gasto/socio mantiene importes y desglose correctos; factura/classificación automática no impiden cargo íntegro al socio; partidas sin revisar o informes mezclados bloquean; ambos órdenes informe/conciliación no duplican; reintentos y concurrencia PostgreSQL crean un solo corte; ajuste de varias partidas genera una sola póliza y conserva la original; períodos cerrados y actores no autorizados bloquean; exportación XLSX/CSV/ZIP mantiene una cabecera por corte y trazabilidad. Regresión del resto del flujo contable y validación independiente antes del cierre.

Canon unchanged: el corte inicial completo pertenece a un solo informe y conserva su atomicidad y agrupación. La póliza compensatoria es evidencia de un ajuste autorizado, no otra exportación inicial de gastos individuales. Si se piden cortes parciales sucesivos del mismo informe, eso modifica el contrato del canon y requerirá otro ajuste explícito.

## Estado actual

Implementación en /tmp/samchat-amex-shared-recognition; 318 pruebas pasaron y
validación independiente sin hallazgos abiertos. Cobertura del diff: 87.55%.
Informe de cierre: `rqf-accounting-amex-cut-20261002-closeout.md`.
Pendiente aprobación humana del diff final. Sin commit, push, PR nuevo, migración de producción ni
despliegue. El startup conserva su manejo previo de tablas existentes, pero
excluye las cinco tablas AMEX nuevas mediante `amex_schema_policy.py`: la
migración owner-run es requisito previo de activación.

Límites explícitos: sólo MXN; informes AMEX exclusivos, completos y de un mismo
mes; la identidad requiere el cargo importado y vínculo explícito a sus partidas
del informe antes del corte. La falta de cualquiera de estas condiciones bloquea
el corte con motivo visible. Un informe mixto con gastos pagados por empleado
requiere resolver su clasificación en un flujo separado; no se vuelve a
contabilizar la comprobación de deudores por esta vía.
