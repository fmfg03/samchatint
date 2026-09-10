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

| Area | Current evidence level | Qualification or caveat | Closing evidence still required | Owner |
| --- | --- | --- | --- | --- |
| Gastos, approvals, Payment Run | `deployed_verified` | Runtime release evidence exists; Finance role UAT remains pending. | Finance UAT by role and real evidence | Finance |
| Budgets and reconciliation | `deployed_verified` | Verified only in selected flows; ledger closure remains pending. | reconciliation ledger closure and Finance acceptance | Finance + Accounting |
| CxC, CFDI and collections | `repo_live` | Repo/live surfaces and recent deployment exist; issued-CFDI and collection treatment remains unaccepted. | issued-CFDI and collection UAT | Finance |
| Cashflow and banks | `deployed_verified` | Deployed read model; bank-source completeness and forecast acceptance remain unverified. | bank-source completeness and forecast acceptance | Treasury + Finance |
| Payroll and AMEX | `repo_live` | Components are connected; operational/accounting treatment remains unaccepted. | operational/accounting UAT | Finance + Payroll |
| Governed assistant | `repo_live` | Mostly read/preview; controlled end-to-end acceptance and write limits remain pending. | accepted controlled end-to-end outcome; no broad write authority | Direction + Product |
| Owner Pack and SOUL | `repo_live` | Read models are connected; source coverage and durable publication evidence remain pending. | source coverage and durable publication evidence | Operations |
| FMF, scheduling, logistics | `proposal` | Only partial/proposal evidence is known. | explicit integration or scoped exclusion | Direction |
| Sponsor/media | `proposal` | Current state is partial/projection rather than demonstrated delivery. | persistent approvals, external publication and proof cycle, or exclusion | Commercial + Marketing |
| Training, support, handoff | `proposal` | Documentation is partial; no attendance or acceptance evidence is known. | attendance, access inventory, support terms and acceptance act | Direction + Commercial |

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
