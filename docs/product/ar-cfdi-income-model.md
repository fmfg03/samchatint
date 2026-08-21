# AR / CFDI Income Model

Status: Draft v0.1
Date: 2026-08-20
Scope: F2 conceptual/read-model definition only. No runtime, schema, route, permission, assistant tool, or data changes.

## Authority

Owner: Finance Spine.

Prerequisite:

- D1 freezes `src/devnous/gastos/routes/admin_budget_routes.py` as the canonical presupuesto owner.

Expected-income base:

- `budget_lines` where `budget_direction=income`.
- `budget_line_monthly_plan.expected_income_amount`.
- `src/samchat/budgets/service.py`.

Issued/recognized CFDI income base:

- `budget_cfdi_income_links`.
- `cfdi_reports`.
- `src/devnous/gastos/services/cfdi_income_bridge_service.py`.

Collection/cash base:

- `unknown` for AR S1.
- `bank_movements` is a candidate source, but it is not frozen as the canonical collection source until a later matching/conciliation spec validates it.

Rule: AR is not AP/payment-run inverted. AR must separate expected income, issued CFDI, recognized income, collection, balance, due horizon, payer, and reconciliation status.

## AR Object Model

Conceptual fields:

| field | meaning | current source | S1 status |
|---|---|---|---:|
| `ar_item_id` | stable read-model id | derived from budget line, CFDI link, or candidate CFDI | planned |
| `budget_version_id` | presupuesto version | `budget_lines`, `budget_cfdi_income_links` | available |
| `budget_line_id` | expected-income line | `budget_lines.id` | available |
| `tournament_id` | tournament/project owner | `budget_lines`, `budget_cfdi_income_links` | available when linked |
| `phase` | tournament/project phase | `budget_lines.phase`, `budget_cfdi_income_links.phase` | available when linked |
| `payer_rfc` | payer/customer RFC | likely `cfdi_reports.receptor_rfc` or counterparty mapping | unresolved |
| `payer_name` | payer/customer name | likely `cfdi_reports.receptor_nombre` or provider/client catalog | unresolved |
| `expected_income_amount` | expected/budgeted income amount | income budget line and monthly plan | available |
| `expected_month` | expected month | `budget_line_monthly_plan.month_number` | available |
| `cfdi_report_id` | issued CFDI row | `cfdi_reports.id` via link or candidate | available for issued rows |
| `cfdi_uuid` | CFDI UUID | `cfdi_reports.cfdi_uuid` | available for issued rows |
| `issued_amount` | CFDI total | `cfdi_reports.total` | available for issued rows |
| `issued_date` | CFDI date | `cfdi_reports.fecha` | available for issued rows |
| `linked_income_amount` | income amount recognized against budget | `budget_cfdi_income_links.amount` | available for linked rows |
| `recognized_income_date` | income recognition date | `budget_cfdi_income_links.income_date` | available for linked rows |
| `collected_amount` | cash collected | unknown; candidate `bank_movements` after matching | unknown |
| `collection_date` | proven collection date | unknown; candidate `bank_movements.fecha` after matching | unknown |
| `outstanding_amount` | issued amount less collected amount | derived only when collection source is valid | unknown in S1 |
| `outstanding_amount_status` | confidence label for balance | derived | required in S1 |
| `due_date` | expected due date | policy needed: CFDI date + credit days or contract terms | unresolved |
| `status` | AR lifecycle status | derived from fields above | required in S1 |

## Status Semantics

`planned`

- Income budget exists.
- No CFDI is linked to that expected-income line.

`issued_unlinked`

- PSP-issued CFDI candidate exists.
- It is not linked to a budget income line for the selected version.

`issued_linked`

- CFDI is linked to an income budget line through `budget_cfdi_income_links`.

`recognized`

- Income is counted as real income through an active `budget_cfdi_income_links` row.
- This does not prove cash collection.

`collection_unknown`

- Collection/cash source is not validated for the row.
- Default S1 status for linked/recognized income when no approved collection source exists.

`partially_collected`

- Allowed only after a canonical collection source and matching rule are approved.

`collected`

- Allowed only when collection is proven by the approved collection source.

`overdue`

- Allowed only when a due date exists and outstanding amount is proven or clearly labeled as estimated.

## S1 Read Model

F2 S1 should be read-only.

Implementation:

- `src/samchat/ar/service.py`
- `build_ar_read_model(...)`
- `GET /admin/finanzas/cuentas-por-cobrar`
- `src/samchat/ar/admin_ui.py`

The admin consumer is read-only and uses `build_ar_read_model(...)`; it does
not calculate collected, paid, outstanding, or cashflow amounts.

F2 S3 pre-matching implementation:

- `src/samchat/ar/matching.py`
- `build_ar_matching_workbench(...)`
- `src/samchat/ar/admin_ui.py`
- `GET /admin/finanzas/cuentas-por-cobrar`

This is candidate analysis only. `bank_movements` is read as tentative evidence,
not collection proof. The workbench may emit `candidate_match`,
`manual_match_required`, `payer_gap`, `collection_unknown`, and
`unmatched_bank_inflow`, but it must not emit collected/paid states.

F2 S4 accepted-match authority implementation:

- `src/samchat/ar/collection_matches.py`
- `ar_collection_matches`
- `ar_collection_match_audit_log`
- `POST /admin/finanzas/cuentas-por-cobrar/matches/accept`
- `POST /admin/finanzas/cuentas-por-cobrar/matches/{match_id}/reverse`

Accepted AR collection matches are the first implemented AR collection
authority. They are separate from `bank_movements.conciliacion_estado`, do not
modify legacy bank reconciliation, and currently allow only one AR item to one
bank inflow with no partial, split, overpayment, or many-to-one semantics.
Only active accepted matches may produce `matched_collected`.

Inputs:

- expected income lines from canonical budgets;
- monthly expected income from budget monthly plan;
- active CFDI income links from `budget_cfdi_income_links`;
- PSP CFDI income candidates from `list_psp_cfdi_income_candidates`;
- candidate payer data from `cfdi_reports`;
- collection state set to `unknown` unless a later spec validates bank/cash matching.

Output groups:

- `expected_income`: budget income rows without linked CFDI coverage;
- `issued_linked`: CFDI rows linked to budget income lines;
- `issued_unlinked`: PSP CFDI candidates not linked to the selected budget version;
- `collection_gaps`: linked/issued rows where collection is unknown;
- `matching_gaps`: rows missing payer, tournament, phase, budget line, or amount confidence.

Balance rule for S1:

- If collection source is unknown, set `outstanding_amount=null` and `outstanding_amount_status=unknown`.
- If collection source is later validated, set `outstanding_amount=issued_amount-collected_amount` and label the source.
- Do not present unknown outstanding amounts as confirmed receivables.

## Dependencies

F2 depends on:

- D1 presupuesto owner freeze;
- canonical income budget lines;
- CFDI income bridge;
- clear distinction between issued, recognized, and collected income.

F2 does not depend on:

- assistant tools;
- cashflow F3;
- new DB tables;
- legacy presupuesto handlers.

F3 cashflow may consume AR S1 only after AR fields label collection status and outstanding confidence.

## Gaps

Collection source:

- `bank_movements` exists and may support collection, but it needs a matching/conciliation policy before AR treats it as canonical.

Payer normalization:

- `cfdi_reports.receptor_rfc` and `receptor_nombre` may identify customer, but relationship to `proveedores_clientes` is not frozen.

Due-date policy:

- `dias_credito` appears in cash-flow UX, but AR needs an approved policy for default credit days, contractual terms, overrides, and no-due-date rows.

Partial collection:

- No approved rule yet for partial collection, overpayment, credit notes, or multi-payment invoices.

Unreconciled rows:

- PSP CFDI candidates may lack tournament/phase/budget-line mapping until linked.

Outstanding balance:

- Cannot be confirmed until collection source and matching are approved.

## Next Decisions

### F2-D1. Collection Source

Decide whether `bank_movements` plus a matching table/rule becomes the canonical collection source for AR.

Decision for now:

- AR S1 launches with `collection_unknown`.
- `bank_movements` is candidate evidence, not the canonical AR collection source yet.
- Existing treasury, cash-flow, CxC, and reconciliation routes may inform later matching work, but they do not freeze AR collection authority in S1.
- A bank movement can become collection proof only after an approved matching policy or an explicit accepted match that binds the movement to the AR/CFDI income item.

## F2-D1 Collection Source Decision

AR S1 must not block on bank matching. It should expose expected income, issued/linked CFDI income, and collection gaps while labeling collection as unknown unless collection proof is available under an approved contract.

Bank movement evidence fields:

| field | meaning for AR | S1 treatment |
|---|---|---|
| `signo` | `+` can indicate inflow | candidate only |
| `importe` | amount available for match | candidate only |
| `fecha` | possible collection date | candidate only |
| `rfc_ordenante` | possible payer RFC | candidate only |
| `nombre_ordenante` | possible payer name | candidate only |
| `referencia_bancaria` | possible transfer reference | candidate only |
| `clave_rastreo` | possible payment trace | candidate only |
| `concepto_banco` | possible text reference to CFDI/customer | candidate only |
| `proveedor_cliente_id` | normalized counterparty if import matched it | candidate only |
| `conciliacion_estado` | existing reconciliation status | not AR proof unless linked by policy |

Collection matching states:

`candidate_match`

- Bank inflow resembles an AR/CFDI income item by amount, payer text, RFC, date, or reference.
- It does not prove collection.

`manual_match_required`

- Multiple plausible AR items exist, evidence is incomplete, or confidence is below the approved threshold.

`matched_collected`

- Allowed only after an approved policy or explicit accepted match binds a bank movement to the AR/CFDI income item.

`partial_match`

- Bank evidence covers less than the issued or recognized income amount.
- Requires approved partial-collection semantics.

`overpayment`

- Bank evidence exceeds the issued or recognized income amount.
- Requires approved overpayment/advance semantics.

`unmatched_bank_inflow`

- Bank inflow exists but cannot be tied to an AR/CFDI income item.

`payer_gap`

- CFDI or budget row lacks reliable payer identity for matching.

`collection_unknown`

- Default AR S1 collection state when no approved collection proof exists.

Rules for S1:

- `conciliacion_estado=high` is not automatically AR collection proof.
- Matching by amount alone is not enough.
- Matching by RFC/name alone is not enough.
- A single bank movement may cover multiple CFDI.
- A single CFDI may be paid partially or across multiple movements.
- Unknown collection must remain visible as a gap, not hidden behind a zero balance.

## D2 Collection Match Authority

Decision:

- The future canonical AR collection authority is a dedicated table such as
  `ar_collection_matches`, not `bank_movements.conciliacion_estado`.
- `bank_movements.conciliacion_estado` can remain useful for accounting or
  treasury reconciliation, but it is not sufficient to mark AR as collected.
- Cashflow, planning, assistant answers, and outstanding-balance views may
  consume accepted AR collection matches only after this authority exists.

Minimum acceptance requirements:

- The AR item must be identifiable as linked CFDI income or an approved PSP
  CFDI income candidate.
- The bank evidence must be a specific inflow movement.
- The amount must be compatible under an approved tolerance.
- Identity must match by exact payer RFC, or be explicitly reviewed by an
  authorized user with a reason.
- The accepting user must have finance authority.
- The accepted match must write an audit trail with before/after evidence.
- Undo/reversal must also be audited.

Prohibited in the first authority implementation:

- no split payments;
- no partial payments;
- no overpayments;
- no many-to-one or one-to-many collection matches;
- no acceptance by amount alone;
- no acceptance by RFC/name alone;
- no silent promotion from treasury or accounting reconciliation routes.

Future accepted states:

`accepted_collection_match`

- A user accepted the binding between one AR item and one bank inflow under the
  approved policy.

`matched_collected`

- AR collection state derived from an active accepted collection match.

`manual_match_rejected`

- A candidate was reviewed and rejected; it must not keep surfacing as a strong
  candidate without new evidence.

`match_reversed`

- A previously accepted match was undone through an audited reversal.

Downstream rule:

- Candidate evidence can support review queues only.
- Accepted collection matches can support collected amount, collection date,
  cashflow, planning, assistant finance answers, and outstanding balance.

### F2-D2. Payer Identity

Decide whether payer identity is taken from CFDI receptor fields, `proveedores_clientes`, or a new mapping layer.

### F2-D3. Due Date Policy

Decide default `dias_credito`, project overrides, and whether due date can come from contracts/documents.

### F2-D4. S1 Build Scope

Decide whether the first implementation is:

- service-only read model;
- `/admin/presupuestos` income-panel enhancement;
- `/admin/finanzas` AR panel;
- standalone `/admin/finanzas/cuentas-por-cobrar`;
- assistant-readable only after UI/service stabilizes.

## Validation Plan

```bash
rg -n "AR Object Model|collection_unknown|issued_linked|budget_cfdi_income_links|bank_movements|outstanding_amount_status" docs/product
git diff -- docs/product/ar-cfdi-income-model.md docs/product/finance-spine-map.md docs/product/samchat-product-spine.md
git status --short
```
