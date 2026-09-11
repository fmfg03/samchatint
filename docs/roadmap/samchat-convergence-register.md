# SamChat convergence register

Status: active planning baseline, 2026-09-10.

## Evidence baseline

| Surface | Observed state | Evidence |
| --- | --- | --- |
| Repository main | `dcbeea8423030e0123101a35378d765ea3c96f32` | `origin/main` and local `main` aligned |
| Production release | `gastos-prod-dcbeea842-reimbursement-budget-guard` | systemd WorkingDirectory and release manifest |
| Runtime | healthy, ready, zero restarts | `/healthz`, `/readyz`, release manifest |
| Release gates | passing | registration operational surface and accepted regressions |
| Route snapshot migration | applied and verified | `eligible_empleado_ids` exists; two routes have holders |

The three top-level `SAMCHAT_*_2026-09-10.md` files are versioned canonical
sources. Their SHA-256 values are an integrity receipt, not permission to alter
them. Any change requires an explicit, human-reviewed PR that records the
evidence, date, and reason for the update and refreshes this table in the same
change. A PR that does not change the canon must declare `Canon unchanged` and
state why. Automation may verify integrity, but it must never author or approve
canon edits silently.

| Protected source path | SHA-256 at Line 0 baseline |
| --- | --- |
| `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md` | `beeac21b5e390070550d63335c892c5407abec1eae49136a61d3598a6048a51a` |
| `SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md` | `f930556b0c6002d8d6242e5591fdce72362d922a39a0746d4fac04538580e433` |
| `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md` | `04bf69b5e3cb1d37a727fb688b838fa4f95501af4e2638ab78ed2d1fcff22e0c` |

| Canon amendment date | Affected sources | Reason and evidence | Evidence state |
| --- | --- | --- | --- |
| 2026-09-11 | Product and Engineering canons | This amendment governs the pending correction to #314/#315 from an external-client interpretation to internal Direction, position-scoped boards and reports. Production remains at `81335f8ddd7e103839719f62235a14d11bb2bd47` with the incorrect client-based interpretation; the correction is only an uncommitted isolated-worktree diff with focused authorization/reporting tests and route-contract validation. | `repo_live` only after merge; `deployed_verified` only after a new release plus authenticated smoke. UAT and real scope configuration remain pending; not `business_accepted`. |

## Work lanes and gates

| ID | Lane | Owner | Start gate | Exit evidence |
| --- | --- | --- | --- | --- |
| L0 | Release and source convergence | Release owner | baseline receipt | local main aligned, rescue ref retained, sources unchanged |
| L1 | Financial reconciliation | Finance + Accounting | approved read-only inventory | approved remediation receipt or owned residual register |
| L2 | Integral Finance UAT | Finance | L1 critical blockers classified | explicit UAT decision and case evidence |
| L3 | Contractual closeout | Direction + Commercial | current delivery evidence | signed scope/evidence/observation matrix |

## Line 0 receipt

| Item | Value |
| --- | --- |
| Prior local main ref | `09b2c6b137ecf04d13726bd670e565f00fd7a797` |
| Durable preservation ref | `origin/rescue/main-pre-convergence-20260910` |
| Aligned main ref | `dcbeea8423030e0123101a35378d765ea3c96f32` |
| Source file policy | versioned canon; edit only through an explicit human-reviewed PR with evidence and refreshed hash receipt |

## Operating rules

1. A repository commit is not production evidence; a production release is not
   business acceptance.
2. Production changes require an immutable release, backup, explicit migration
   authority when applicable, release gates, and manifest verification.
3. Reconciliation begins read-only. Any data mutation requires its own dry-run,
   target inventory, approval, and post-apply receipt.
4. UAT failures become bounded defects only after reproduction and ownership are
   recorded. Requested features are not silently reclassified as defects.
5. This register records facts and decisions; it does not confer authority to
   deploy, mutate data, or accept contractual scope.
