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

The three top-level `SAMCHAT_*_2026-09-10.md` files are protected source inputs.
They are not tracked or edited by this register. Their SHA-256 values are held in
the Line 0 baseline receipt before any future editorial decision.

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
| Preservation ref | `rescue/main-pre-convergence-20260910` |
| Aligned main ref | `dcbeea8423030e0123101a35378d765ea3c96f32` |
| Source file policy | preserve, do not stage, move, delete, or edit without separate authority |

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
