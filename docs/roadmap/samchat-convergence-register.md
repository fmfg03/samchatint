# SamChat convergence register

Status: active planning baseline, 2026-09-10.

## Evidence baseline

| Surface | Observed state | Evidence |
| --- | --- | --- |
| Repository main | `090188c5e35e9b0733cdb4fa325db46331f4cdfc` | #320 merged to `main` after the deployed #319 baseline |
| Production release | `gastos-prod-06ff7d9-direccion-authorization` | verified systemd WorkingDirectory and release manifest |
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
| `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md` | `0e40aecfc98be3196aa8a96dc1f1f2bbc8a5bb4239602b2600655a6b15d40a56` |
| `SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md` | `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985` |
| `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md` | `d17b7151c54f59e7aa4f091f7aa26734255466952212d72fdeb73605803ab2b2` |

| Canon amendment date | Affected sources | Reason and evidence | Evidence state |
| --- | --- | --- | --- |
| 2026-09-11 | Product and Engineering canons | This amendment governs the pending correction to #314/#315 from an external-client interpretation to internal Direction, position-scoped boards and reports. Production remains at `81335f8ddd7e103839719f62235a14d11bb2bd47` with the incorrect client-based interpretation; the correction is only an uncommitted isolated-worktree diff with focused authorization/reporting tests and route-contract validation. | `repo_live` only after merge; `deployed_verified` only after a new release plus authenticated smoke. UAT and real scope configuration remain pending; not `business_accepted`. |
| 2026-09-12 | Product and Engineering canons | Human-approved expansion of `/direccion/tableros` to cross-domain, read-only Operations, Finance, and Marketing visibility inside assigned Direction portfolio/tournament scope. Evidence: scoped budget and tournament SOUL/entity-dossier integration, explicit gap inventory, aggregate age handling, focused tests, lint and diff validation. | Worktree implementation under review; not merged, deployed, UAT-validated, or business accepted. |
| 2026-09-19 | Engineering canon | Human-approved correction for PR #350: its required CI gate replaces the obsolete Python 3.11/3.12 and non-blocking-security description with the Python 3.12-only direct-suite, exact pytest/Bandit baselines, changed-code coverage, and aggregate-gate contract. Evidence: focused contract tests, exact Bandit baseline verification, and diff hygiene in the isolated worktree. | Worktree implementation under review; not merged, deployed, or business accepted. |
| 2026-09-29 | Product and Engineering canons | Human-approved accounting invariant: one atomic COI policy per `INFORME`, with all active expense movements and CFDI blocks grouped under one header; incomplete or cross-period reports originally failed closed without partial status updates. The cross-period restriction is superseded by the human-approved 2026-10-05 amendment below. Evidence: isolated-worktree implementation and focused exporter, route, batch, and ZIP regression tests. | Worktree implementation under review; not merged, deployed, authenticated-UAT-validated, or business accepted. |
| 2026-10-05 | Product and Engineering canons | Explicit human approval to permit normal reports with expense dates in different months, using one approval-month policy period for monthly discovery and Finance batches. Missing approval dates remain blockers. AMEX requires its immutable initial cut and that cut's accounting month, retaining cut-creation controls. Evidence: PR #458 shared period predicates, SQL-backed discovery tests, complete CSV/XLSX/ZIP exports and frozen-cut regressions. | Source changes under PR review; deployment, authenticated UAT and business acceptance remain pending. |

| 2026-10-05 | Product, Engineering and Sweep canons | Human-approved final diff and canon amendment for the first Copa Telmex registration PostgreSQL delivery. Explicit tournament/edition catalog, shared assistant/Direction read projection, and early exclusion of alternate SOUL registration reads. Evidence: `docs/roadmap/ctt-postgres-registration-first-delivery.md`; 209 tests passed, 99.45% changed-statement coverage, independent review with no pending defects. General lint/type validation remains partial due to inherited debt. | Approved isolated-worktree candidate; not committed, merged, deployed, imported, UAT-validated, or business accepted. |
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

## Proposed 2026-10-02 amendment — requires human PR review

Francisco authorized SUPERADMIN read supervision of all active local-installation
tournaments at 18:27 UTC. Product and engineering canons record that narrow
exception and independent documentary source coverage. Other profiles, explicit
denials and all write authority remain unchanged. Evidence and limitations:
`docs/evidence/direction-report-redesign/README.md`. Updated hashes above describe
the proposed draft, not deployment or accepted UAT. Sweep canon unchanged.

## Proposed 2026-10-06 amendment — requires human PR review

Francisco requested ordered partial paid-advance approvals with a confirmed
applicant motive, approver comments, an open report until zero, and idempotent
incremental accounting. Product and engineering canons propose explicit child
`INFORME` ownership while preserving atomic export per child and the AMEX cut.
Evidence and release limitations: `docs/roadmap/partial-advance-comprobaciones.md`.
The refreshed hashes describe a proposed draft; human canon review, production
migration, deployment and authenticated Finance UAT remain pending.
Canon unchanged for the sweep: it is the historical installation inventory;
this work makes no new production-observation claim.
