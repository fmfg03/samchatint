# RQF-ACCOUNTING-LAMINAR-001 — Especificación técnica

Status: READY_FOR_REVIEW
Date: 2026-08-27
Depends on: `rqf-accounting-laminar-001-story.md`
Scope of this cut: contrato ejecutable, servicios de contabilización y cableado de eventos productivos sin backfill histórico.

## 1. Objetivo técnico

Definir y cablear una frontera única entre las transiciones operativas y la generación de
pólizas. La frontera debe producir una decisión determinista, balanceada e
idempotente para las once reglas contables aprobadas, bloqueando la transición
cuando falte configuración contable necesaria.

## 2. Fuentes de verdad

Orden de precedencia:

1. Tipo de evento y transición autorizada del documento.
2. Concepto presupuestal asignado y snapshot de sus cuentas de gasto/pasivo.
3. Desglose fiscal validado del CFDI o regla explícita de No Deducibles.
4. Clasificación explícita del beneficiario como empleado o socio.
5. Mapeo explícito y activo de tarjeta AMEX a pasivo.
6. Taxonomía canónica fijada en esta especificación.

No son fuentes de verdad:

- texto libre, coincidencia parcial de nombres o el rol del capturista;
- la primera cuenta bancaria o pasiva encontrada;
- un valor actual del catálogo que contradiga el snapshot del evento previo;
- referencias `1700-*`; son errores de transcripción y no se aceptan como cuenta nueva;
- el estado mostrado en UI sin la transición durable que lo respalda.

## 3. Contratos de datos

### 3.1 AccountingEvent

Campos mínimos:

```text
event_id                 UUID durable del evento
rule_id                  LAM-001 .. LAM-011
source_type              documento | informe | amex_reconciliation | amex_payment
source_id                UUID durable de la entidad
source_state_version     versión monotónica de la transición
transition               approved | paid | reconciled
effective_at             fecha/hora contable
actor_id                  actor que autorizó la transición
beneficiary_id            beneficiario económico, si aplica
beneficiary_kind          employee | partner | null
budget_concept_id         concepto gobernado, si aplica
amex_card_account_id      tarjeta gobernada, si aplica
economic_spend_id         identidad del gasto económico, si aplica
policy_version            ACCOUNTING-LAMINAR-001
```

### 3.2 ResolvedAccountingContext

La resolución se congela antes de escribir:

```text
expense_account_ids      cuentas de gasto resueltas
non_deductible_account   cuenta aplicable, si existe importe
tax_lines                cuenta e importe por impuesto
liability_account_id     pasivo de presupuesto o AMEX
debtor_account_id        subcuenta canónica 1170, si aplica
bank_account_id          cuenta exacta 1120-001-001, si aplica
amounts                   Decimal cuantizado a 0.01
source_aliases            alias/typo observado y bloqueado, si aplica
resolution_evidence      IDs del catálogo/mapeo que prueban cada cuenta
```

No se admite un contexto parcialmente resuelto.

### 3.3 AccountingReceipt

```text
idempotency_key          clave única estable
event_id                 evento consumido
policy_version            ACCOUNTING-LAMINAR-001
accounting_owner         propietario exclusivo del hecho económico
outcome                  POSTED | NO_POSTING_REQUIRED | ALREADY_POSTED | BLOCKED
poliza_id                nullable
line_fingerprint         hash ordenado de debe/haber/cuenta/importe
blocked_reason_codes     lista cerrada de errores
created_at               timestamp
```

## 4. Taxonomía y normalización

Constantes canónicas:

```text
EMPLOYEE_DEBTOR_PREFIX   = 1170-001-
PARTNER_DEBTOR_PREFIX    = 1170-002-
ODILON_AMEX_DEBTOR       = 1170-002-004
SANTANDER_BANK           = 1120-001-001
AMEX_LIABILITIES         = {
  2120-002-062, 2120-002-063, 2120-002-064, 2120-002-065,
  2120-002-066, 2120-002-067, 2120-002-100
}
```

Tratamiento de alias de entrada:

```text
1700-001-XXX = typo observado para 1170-001-XXX
1700-002-XXX = typo observado para 1170-002-XXX
```

Condiciones:

- `1700` no es segmento contable válido para pólizas nuevas;
- la frontera debe resolver contra una cuenta `1170` activa por identidad explícita;
- si sólo aparece `1700` o la correspondencia es ambigua, el evento queda `BLOCKED`;
- nunca se persiste una línea `1700`;
- no se ejecuta migración ni backfill.

## 5. Tabla de transición y póliza

| Regla | Evento | Debe | Haber | Propietario contable |
| --- | --- | --- | --- | --- |
| LAM-001 | Transferencia aprobada | Gasto de concepto + No Deducibles + impuestos | Pasivo del concepto | `transfer_approval` |
| LAM-002 | Transferencia pagada | Pasivo congelado en LAM-001 | `1120-001-001` | `transfer_payment` |
| LAM-003 | Anticipo aprobado | Sin póliza | Sin póliza | `advance_approval` |
| LAM-004 | Anticipo pagado | Deudor `1170-001-*` o `1170-002-*` | `1120-001-001` | `advance_payment` |
| LAM-005 | Reembolso aprobado | Sin póliza | Sin póliza | `reimbursement_approval` |
| LAM-006 | Reembolso pagado | Deudor `1170-001-*` o `1170-002-*` | `1120-001-001` | `reimbursement_payment` |
| LAM-007 | Informe de comprobación aprobado | Gasto + No Deducibles + impuestos | Deudor `1170-001-*` o `1170-002-*` | `advance_expense_report` |
| LAM-008 | Informe de reembolso aprobado | Gasto + No Deducibles + impuestos | Deudor `1170-001-*` o `1170-002-*` | `reimbursement_expense_report` |
| LAM-009 | Informe AMEX aprobado | Gasto + No Deducibles + impuestos | `1170-002-004` | `amex_expense_report` |
| LAM-010 | Conciliación AMEX autorizada | Gasto + No Deducibles + impuestos | Pasivo de tarjeta permitido | `amex_reconciliation` |
| LAM-011 | Pago AMEX confirmado | Pasivo de la tarjeta | `1120-001-001` | `amex_payment` |

Para LAM-001/LAM-002, el pasivo se resuelve una vez al aprobar y se reutiliza
desde el recibo. Para LAM-010/LAM-011 ocurre lo mismo con el pasivo de tarjeta.

## 6. Resolución por tipo de dependencia

### 6.1 Concepto presupuestal

El concepto debe tener cuenta de gasto activa y, cuando la regla lo requiere,
pasivo activo. No se sugiere una cuenta contable separada: el concepto es la
unidad gobernada y sus cuentas se resuelven como parte del snapshot.

### 6.2 No Deducibles e impuestos

Sólo se crean líneas cuyo importe sea mayor a cero. Cada importe debe tener una
cuenta explícita y evidencia de resolución. La suma del gasto base, No
Deducibles, impuestos y retenciones debe reconciliar con la contrapartida.

### 6.3 Deudor

La identidad económica es el beneficiario, no el solicitante/capturista. La
clasificación `employee`/`partner` debe provenir de un dato gobernado. El nombre
sólo puede utilizarse como dato de presentación, nunca para cambiar de segmento.

### 6.4 Banco Santander

La resolución exige el código exacto `1120-001-001`, activo y de naturaleza
banco. Se prohíbe seleccionar “la primera cuenta tipo banco”.

### 6.5 AMEX

La tarjeta se identifica con `amex_card_account_id`; sus últimos cuatro dígitos
son un dato de verificación, no la llave primaria. El pasivo activo debe estar
en el allowlist. Un mapeo faltante, duplicado o fuera del allowlist bloquea.

## 7. Idempotencia y concurrencia

Clave lógica:

```text
sha256(
  policy_version | rule_id | source_type | source_id |
  source_state_version | accounting_owner
)
```

Requisitos de implementación futura:

- índice único de base de datos sobre los componentes semánticos o sobre la
  clave materializada;
- creación de recibo y póliza en una sola transacción;
- retry tras timeout retorna `ALREADY_POSTED` y el recibo original;
- dos workers concurrentes no pueden crear dos pólizas;
- la identidad de evento no depende de timestamp, request ID HTTP ni actor.

El índice existente por archivo/tipo/número de póliza no sustituye esta
restricción semántica.

## 8. Propiedad exclusiva y doble contabilización

`economic_spend_id` une el consumo económico con su propietario contable.

- LAM-007, LAM-008 y LAM-009 son clasificaciones mutuamente excluyentes del
  informe.
- LAM-009 y LAM-010 no pueden cargar el mismo consumo.
- Si ya existe un recibo `POSTED` con otro propietario para el mismo
  `economic_spend_id`, el segundo evento termina `BLOCKED` con
  `economic_spend_already_owned`.
- No se genera automáticamente una reversa o reclasificación. Esa capacidad
  requiere otra historia, autoridad explícita y evidencia de compensación.

## 9. Fail-closed y códigos de bloqueo

Lista mínima:

```text
missing_budget_concept
missing_expense_account
missing_budget_liability
missing_tax_account
missing_non_deductible_account
missing_beneficiary_classification
missing_debtor_account
ambiguous_debtor_account
invalid_1700_alias
missing_santander_account
ambiguous_santander_account
missing_amex_card_mapping
invalid_amex_liability
amount_not_cent_quantized
unbalanced_journal
economic_spend_already_owned
source_transition_not_authorized
```

Una resolución bloqueada no persiste póliza ni líneas. El recibo bloqueado sí
puede conservarse como evidencia diagnóstica, sin efectos contables.

## 10. Corte prospectivo y datos históricos

- `effective_from` se configura al activar la política.
- Eventos con fecha/versión anterior quedan fuera y no se reejecutan.
- La nueva restricción de idempotencia puede agregarse prospectivamente, pero
  no autoriza rellenar recibos o pólizas antiguas.
- No se renumeran cuentas históricas `1700`, no se cambian pólizas y no se
  generan reversas automáticas.
- Un proyecto posterior de saneamiento debe usar conciliación, aprobación y
  recibos propios.

## 11. Frontera de autoridad

El motor recibe una transición durable ya autorizada. Debe verificar:

- tipo de evento y versión;
- actor y evidencia de autorización;
- que la transición todavía sea vigente;
- que el documento no esté cancelado, reversado o superado por otra versión.

El motor no llama rutas de aprobación/pago ni muta estados operativos. Una
póliza fallida no revierte silenciosamente el workflow; expone un bloqueo para
Contabilidad.

## 12. Plan de pruebas conductuales

| ID | Conducta | Evidencia esperada |
| --- | --- | --- |
| CT-LAM-001..011 | Una prueba por cada regla | Líneas y cuentas exactas, balance y recibo. |
| CT-LAM-012 | Alias `1700` observado | Bloquea si no existe cuenta `1170` activa e inequívoca; nunca persiste `1700`. |
| CT-LAM-013 | Alias `1700` ambiguo | `BLOCKED: invalid_1700_alias`. |
| CT-LAM-014 | Santander faltante/duplicado | No usa otro banco; evento bloqueado. |
| CT-LAM-015 | Beneficiario empleado/socio | Selecciona segmento por clasificación, no por nombre. |
| CT-LAM-016 | Reintento secuencial | Una póliza, mismo recibo. |
| CT-LAM-017 | Reintento concurrente | Una póliza, un recibo durable. |
| CT-LAM-018 | Colisión LAM-009/LAM-010 | Segundo propietario bloqueado. |
| CT-LAM-019 | Cuenta/mapeo faltante | Cero pólizas parciales. |
| CT-LAM-020 | Evento previo al corte | No modifica ni completa historia. |
| CT-LAM-021 | Catálogo cambia tras aprobación | Pago usa snapshot del recibo previo. |
| CT-LAM-022 | Journal desbalanceado | Bloqueo antes de persistir. |

`tests/unit/gastos/test_accounting_laminar_executable_spec.py` materializa una
primera especificación ejecutable. No llama aún a servicios productivos y no es
evidencia de wiring. El cierre de implementación requiere adaptar la misma
matriz a servicios reales, transacciones y base de datos concurrente.

## 13. Secuencia de implementación posterior

1. Crear tipos de evento, resolución y recibo sin activar consumidores.
2. Incorporar claves semánticas e índice único prospectivo.
3. Implementar resolutores fail-closed para concepto, deudor, banco y AMEX.
4. Implementar un generador de líneas puro y probar las once reglas.
5. Conectar un evento por vez detrás de feature flags.
6. Ejecutar shadow comparison sin writes contra eventos nuevos.
7. Activar por tipo de evento después de reconciliación firmada.

## 14. No claims

- Esta spec no demuestra que el código actual cumpla las once reglas.
- La prueba ejecutable incluida valida el contrato, no el wiring productivo.
- No se activan pólizas, migraciones, backfills ni correcciones históricas.
- No se resuelve automáticamente la colisión económica entre LAM-009 y LAM-010;
  se bloquea para evitar doble gasto.

