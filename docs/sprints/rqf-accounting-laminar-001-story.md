# RQF-ACCOUNTING-LAMINAR-001 — Flujo contable laminar

Status: READY_FOR_REVIEW
Date: 2026-08-27
Branch: `codex/rqf-accounting-laminar-001`
Scope: historia funcional, límites de cierre, servicios de contabilización y cableado inicial de eventos productivos.

## Problema

SamChat tiene varios disparadores contables para solicitudes, anticipos,
reembolsos, informes de gastos y AMEX. Esos disparadores no cuentan hoy con un
contrato único que fije:

- el momento exacto en que nace cada asiento;
- las cuentas canónicas que deben intervenir;
- la relación entre concepto presupuestal, gasto, impuestos, no deducibles y
  contrapartida;
- la diferencia entre empleados y socios;
- la cuenta bancaria autorizada;
- la propiedad contable exclusiva de un consumo AMEX;
- el comportamiento ante reintentos, configuración incompleta o datos
  ambiguos.

Sin ese contrato, un mismo hecho económico puede contabilizarse dos veces,
usar una cuenta inferida o generar una póliza parcialmente válida.

## Historia de usuario

Como responsable de Contabilidad de Plataforma Sports,
quiero que cada transición de negocio genere exactamente el asiento definido
por la política contable aprobada,
para que las pólizas sean balanceadas, trazables, idempotentes y no dependan de
heurísticas ni de la pantalla desde la que se ejecutó la transición.

## Resultado esperado

El flujo es laminar cuando cada evento de negocio tiene un solo propietario
contable, una sola resolución de cuentas, un solo recibo y un resultado
determinista:

1. `POSTED`: se creó una póliza balanceada una sola vez.
2. `NO_POSTING_REQUIRED`: la regla prescribe explícitamente que no hay asiento.
3. `ALREADY_POSTED`: el mismo evento fue reintentado y se devuelve el recibo
   existente sin duplicar líneas.
4. `BLOCKED`: falta configuración, la clasificación es ambigua o el asiento no
   balancea; no se persiste ninguna póliza parcial.

## Taxonomía contable canónica

| Uso | Cuenta / segmento canónico | Regla |
| --- | --- | --- |
| Deudores empleados | `1170-001-XXX` | Cuenta activa del empleado, resuelta por identidad explícita. |
| Deudores socios | `1170-002-XXX` | Cuenta activa del socio, resuelta por identidad explícita. |
| AMEX de Odilón en informe | `1170-002-004` | Cuenta fija para la regla 9. |
| Banco Santander | `1120-001-001` | Única cuenta bancaria de abono en estas reglas. |
| Pasivos AMEX por tarjeta | `2120-002-062`, `2120-002-063`, `2120-002-064`, `2120-002-065`, `2120-002-066`, `2120-002-067`, `2120-002-100` | Se elige por la tarjeta AMEX identificada. |

`1700-001-XXX` y `1700-002-XXX` no son segmentos nuevos ni objetivo de una
migración. Son errores de transcripción de `1170-001-XXX` y `1170-002-XXX`.
Nunca se persiste una línea nueva con prefijo `1700`. Si aparece un valor
`1700`, la frontera debe resolver por identidad explícita contra una cuenta
canónica `1170` activa; si no puede hacerlo de forma inequívoca, bloquea el
asiento.

## Criterios de aceptación: once reglas de negocio

### AC-LAM-001 — Transferencia aprobada

Al pasar una Solicitud de Transferencia a `APROBADA`:

- Debe: cuentas de gasto definidas por el concepto presupuestal, más las líneas
  aplicables de No Deducibles e impuestos.
- Haber: cuenta de pasivo definida en ese mismo concepto presupuestal.
- Si falta la cuenta de gasto, el pasivo explícito o el desglose necesario, el
  evento queda `BLOCKED`; no se usa un pasivo genérico.

### AC-LAM-002 — Transferencia pagada

Al pasar la misma solicitud a `PAGADA`:

- Debe: el pasivo congelado en el recibo de la aprobación.
- Haber: `1120-001-001` Banco Santander.
- Un cambio posterior al catálogo no altera la cuenta ya resuelta en la
  aprobación.

### AC-LAM-003 — Anticipo aprobado

Al pasar una Solicitud de Anticipo a `APROBADA` no se genera póliza. El evento
produce un recibo `NO_POSTING_REQUIRED` para impedir que otro camino lo trate
como pendiente.

### AC-LAM-004 — Anticipo pagado

Al pasar una Solicitud de Anticipo a `PAGADA`:

- Debe: `1170-001-XXX` si el beneficiario es empleado, o `1170-002-XXX` si es
  socio.
- Haber: `1120-001-001` Banco Santander.
- La clasificación empleado/socio es un dato explícito; no se infiere por
  nombre.

### AC-LAM-005 — Reembolso aprobado

Al pasar una Solicitud de Reembolso a `APROBADA` no se genera póliza. El evento
produce un recibo `NO_POSTING_REQUIRED`.

### AC-LAM-006 — Reembolso pagado

Al pasar una Solicitud de Reembolso a `PAGADA`:

- Debe: `1170-001-XXX` si el beneficiario es empleado, o `1170-002-XXX` si es
  socio.
- Haber: `1120-001-001` Banco Santander.

Esta historia conserva la regla suministrada; no reinterpreta el sentido
económico del reembolso ni realiza reclasificaciones históricas.

### AC-LAM-007 — Informe que comprueba anticipo aprobado

Al aprobar un Informe de Gastos clasificado como comprobación de anticipo:

- Debe: gasto por concepto presupuestal, No Deducibles e impuestos aplicables.
- Haber: deudor `1170-001-XXX` empleado o `1170-002-XXX` socio.
- El informe debe estar clasificado de forma exclusiva como comprobación de
  anticipo; no puede ejecutar también la regla 8 o 9.

### AC-LAM-008 — Informe que requiere reembolso aprobado

Al aprobar un Informe de Gastos clasificado como reembolso directo:

- Debe: gasto por concepto presupuestal, No Deducibles e impuestos aplicables.
- Haber: deudor `1170-001-XXX` empleado o `1170-002-XXX` socio.
- El informe debe estar clasificado de forma exclusiva como reembolso directo.

### AC-LAM-009 — Informe AMEX aprobado

Al aprobar un Informe de Gastos clasificado como AMEX bajo esta modalidad:

- Debe: gasto por concepto presupuestal, No Deducibles e impuestos aplicables.
- Haber: `1170-002-004` AMEX Odilón.
- No se acepta otra subcuenta por coincidencia de nombre ni un fallback de
  empleado.

### AC-LAM-010 — Conciliación AMEX

Al autorizar una conciliación cargo AMEX contra sus facturas:

- Debe: gasto por concepto presupuestal, No Deducibles e impuestos aplicables.
- Haber: el pasivo configurado para la tarjeta seleccionada entre
  `2120-002-062`, `2120-002-063`, `2120-002-064`, `2120-002-065`,
  `2120-002-066`, `2120-002-067` y `2120-002-100`.
- La tarjeta debe quedar identificada por un registro activo y unívoco; no se
  elige el primer pasivo disponible.

### AC-LAM-011 — Pago AMEX

Al confirmar el pago de una tarjeta AMEX:

- Debe: el mismo pasivo AMEX congelado para la tarjeta seleccionada.
- Haber: `1120-001-001` Banco Santander.

## Criterios transversales de seguridad

### AC-SAFE-001 — Fail closed

Una cuenta faltante, inactiva o ambigua; una clasificación empleado/socio
ausente; un concepto sin cuenta de gasto o pasivo; una tarjeta sin mapeo; un
importe no cuantizable a centavos; o un asiento desbalanceado produce
`BLOCKED`. No se crea cabecera ni línea parcial y no se usan heurísticas como
“primer banco”, coincidencia débil de nombre o pasivo genérico.

### AC-SAFE-002 — Idempotencia

Cada transición usa una clave estable formada por política, regla, entidad,
versión de transición y propietario contable. Reintentos secuenciales o
concurrentes devuelven el mismo recibo y no crean otra póliza.

### AC-SAFE-003 — Prevención de doble contabilización

Un gasto AMEX tiene un solo propietario contable. Si el mismo hecho económico
ya fue contabilizado por la regla 9, la regla 10 no vuelve a cargar el gasto, y
viceversa. Hasta que exista una reclasificación explícita y aprobada entre
ambas modalidades, la colisión queda `BLOCKED` con evidencia del recibo previo.

### AC-SAFE-004 — Autoridad separada

El servicio contable consume una transición autorizada; no aprueba, paga,
concilia ni cambia el estado operativo por sí mismo. Una póliza nunca constituye
autoridad para avanzar el workflow.

### AC-SAFE-005 — Sin backfill histórico

La política aplica sólo a eventos posteriores al corte de activación. No migra,
reescribe, reversa ni completa pólizas históricas. Los casos anteriores se
mantienen de solo lectura y cualquier corrección requiere un proceso separado,
autorizado y auditable.

## Actores

- Operaciones: origina documentos y evidencia, sin elegir cuentas contables.
- Control Presupuestal: asigna el concepto presupuestal gobernado.
- Aprobador de negocio: autoriza la transición operativa.
- Contabilidad/Finanzas: valida configuración, conciliación y pago.
- Motor contable: resuelve y registra el asiento de forma determinista.
- Auditoría: consulta evento, resolución, póliza y recibo sin mutarlos.

## Fuera de alcance

- Backfill o reparación automática de pólizas históricas.
- Migración masiva `1700` → `1170`.
- Cambio de catálogo contable o creación automática de cuentas.
- Rediseño de UI, COI, DIOT o reportes fiscales.
- Activación de writes contables en producción dentro de esta story/spec.
- Definición de una póliza de reclasificación entre las reglas 9 y 10.

## Evidencia de cierre requerida

- Pruebas de contrato para las once reglas, incluyendo debe/haber exactos.
- Pruebas negativas para cada dependencia obligatoria.
- Prueba de retry secuencial y concurrente sin duplicado.
- Prueba de colisión regla 9/regla 10.
- Prueba de que ninguna línea nueva usa `1700-*`.
- Prueba de que no se mutan pólizas anteriores al corte.
- Trazabilidad desde evento de negocio hasta recibo y póliza.

