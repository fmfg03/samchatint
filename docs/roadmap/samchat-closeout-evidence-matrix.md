# SamChat closeout evidence matrix

Status: planning matrix. Contract claims require the signed SOW, amendments,
invoices and customer acceptance records as primary evidence.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `proposal` | described or targeted in the SOW |
| `repo_live` | connected source, route/tool/UI and tests in main |
| `deployed_verified` | exact active release and observable runtime evidence |
| `business_accepted` | owner UAT or explicit customer acceptance |

## Closure matrix

| Area | Current evidence level | Closing evidence still required | Owner |
| --- | --- | --- | --- |
| Gastos, approvals, Payment Run | deployed verified | Finance UAT by role and real evidence | Finance |
| Budgets and reconciliation | deployed verified in selected flows | reconciliation ledger closure and Finance acceptance | Finance + Accounting |
| CxC, CFDI and collections | repo/live surfaces and recent deployment | issued-CFDI and collection UAT | Finance |
| Cashflow and banks | deployed read model | bank-source completeness and forecast acceptance | Treasury + Finance |
| Payroll and AMEX | connected components | operational/accounting UAT | Finance + Payroll |
| Governed assistant | repo live, mostly read/preview | accepted controlled end-to-end outcome; no broad write authority | Direction + Product |
| Owner Pack and SOUL | connected read models | source coverage and durable publication evidence | Operations |
| FMF, scheduling, logistics | partial/proposal | explicit integration or scoped exclusion | Direction |
| Sponsor/media | partial/projection | persistent approvals, external publication and proof cycle, or exclusion | Commercial + Marketing |
| Training, support, handoff | partial documentation | attendance, access inventory, support terms and acceptance act | Direction + Commercial |

## Required closeout package

1. Scope-to-evidence table: SOW item, actual evidence, status, exclusion or
   change order.
2. Finance UAT decision and bounded defect/observation register.
3. Production release and migration receipts, with backup references.
4. Access, credential-ownership and operational handoff inventory without
   embedding secrets.
5. Commercial statement distinguishing base work, additional work, future
   closure packages, support, third-party costs and unmeasured KPI targets.

## Prohibited conclusions

- Do not present SOW KPI targets as measured outcomes without an accepted
  baseline and observation window.
- Do not present a repository module as deployed or accepted merely because it
  exists in source.
- Do not absorb later work into the original commercial scope without written
  evidence.
