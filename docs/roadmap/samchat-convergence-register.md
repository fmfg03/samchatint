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
| `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md` | `0596b7e502f1d262392e112d86a97bea4b1496a62e98ab60efbf41b9a7a1569d` |
| `SAMCHAT_CODEBASE_SWEEP_REPORT_2026-09-10.md` | `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985` |
| `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md` | `cd1595f7160d6b3a67c90b9bf5de47deaca382fe6065b922d95d8e3e296aac68` |

| Canon amendment date | Affected sources | Reason and evidence | Evidence state |
| --- | --- | --- | --- |
| 2026-10-14 | Product and Engineering canons | Explicit human approval of distribution revision rules (`Sentinel_e34dca62ad888191ae00a160c9ee5b09`); Git publication authorized separately (`Sentinel_a80479c5cb1081919315d4fa293a5777`). Preserve financial lines, amounts and history; approved reconciled successor, week-start month, full-month YTD, fixed categories N/D pending Finance. Evidence: `docs/roadmap/budget-distribution-20261014-candidate.md`. Sweep unchanged: historical inventory, no production observation. | Approved candidate and publication; CI, merge, deployment, identified-case UAT and Finance crosswalk remain pending. |
| 2026-09-11 | Product and Engineering canons | This amendment governs the pending correction to #314/#315 from an external-client interpretation to internal Direction, position-scoped boards and reports. Production remains at `81335f8ddd7e103839719f62235a14d11bb2bd47` with the incorrect client-based interpretation; the correction is only an uncommitted isolated-worktree diff with focused authorization/reporting tests and route-contract validation. | `repo_live` only after merge; `deployed_verified` only after a new release plus authenticated smoke. UAT and real scope configuration remain pending; not `business_accepted`. |
| 2026-09-12 | Product and Engineering canons | Human-approved expansion of `/direccion/tableros` to cross-domain, read-only Operations, Finance, and Marketing visibility inside assigned Direction portfolio/tournament scope. Evidence: scoped budget and tournament SOUL/entity-dossier integration, explicit gap inventory, aggregate age handling, focused tests, lint and diff validation. | Worktree implementation under review; not merged, deployed, UAT-validated, or business accepted. |
| 2026-09-19 | Engineering canon | Human-approved correction for PR #350: its required CI gate replaces the obsolete Python 3.11/3.12 and non-blocking-security description with the Python 3.12-only direct-suite, exact pytest/Bandit baselines, changed-code coverage, and aggregate-gate contract. Evidence: focused contract tests, exact Bandit baseline verification, and diff hygiene in the isolated worktree. | Worktree implementation under review; not merged, deployed, or business accepted. |
| 2026-09-29 | Product and Engineering canons | Human-approved accounting invariant: one atomic COI policy per `INFORME`, with all active expense movements and CFDI blocks grouped under one header; incomplete or cross-period reports originally failed closed without partial status updates. The cross-period restriction is superseded by the human-approved 2026-10-05 amendment below. Evidence: isolated-worktree implementation and focused exporter, route, batch, and ZIP regression tests. | Worktree implementation under review; not merged, deployed, authenticated-UAT-validated, or business accepted. |
| 2026-10-05 | Product and Engineering canons | Explicit human approval to permit normal reports with expense dates in different months, using one approval-month policy period for monthly discovery and Finance batches. Missing approval dates remain blockers. AMEX requires its immutable initial cut and that cut's accounting month, retaining cut-creation controls. Evidence: PR #458 shared period predicates, SQL-backed discovery tests, complete CSV/XLSX/ZIP exports and frozen-cut regressions. | Source changes under PR review; deployment, authenticated UAT and business acceptance remain pending. |

| 2026-10-05 | Product, Engineering and Sweep canons | Human-approved final diff and canon amendment for the first Copa Telmex registration PostgreSQL delivery. Explicit tournament/edition catalog, shared assistant/Direction read projection, and early exclusion of alternate SOUL registration reads. Evidence: `docs/roadmap/ctt-postgres-registration-first-delivery.md`; 209 tests passed, 99.45% changed-statement coverage, independent review with no pending defects. General lint/type validation remains partial due to inherited debt. | Approved isolated-worktree candidate; not committed, merged, deployed, imported, UAT-validated, or business accepted. |
| 2026-10-06 | Product and Engineering canons | Human-approved review-only admission contract for the 11 reconciled Copa Telmex 2026 dossiers. Exact edition, PDF, page, extraction and technical-staff evidence is bound to idempotent review drafts; team, staff and eligibility writes remain blocked pending a separately approved Zaubern staff contract. Evidence: `docs/roadmap/ctt-pilot-batch-admission.md`, 56 focused tests, isolated PostgreSQL migration verification, and independent validation with no critical or important findings. | Approved isolated-worktree candidate; not committed, merged, deployed, migration-applied, data-admitted, UAT-validated, or business accepted. |
| 2026-10-09 | Product and Engineering canons | Human-approved monthly DIOT contract based on `Documento.fecha_pago_efectiva`, with explicit missing/ambiguous-date queues, fail-closed TXT blockers, proportional confirmed shared-CFDI allocation, and IVA withholding read only from XML. Evidence: isolated implementation, 10 focused tests, route import verification, and production classification in a read-only transaction. Sweep canon unchanged because this feature does not revise the historical installation inventory. | Approved isolated-worktree candidate; not committed, merged, deployed, SAT-submitted, authenticated-UAT-validated, or business accepted. |
| 2026-10-13 | Product and Engineering canons | Human-approved technical specification and exact canon amendment for approved-INFORME AMEX recognition, consolidated explicit Finance confirmation and subsequent nonposting bank association. Evidence: `docs/roadmap/amex-approved-informe-20261013-story.md` and `docs/roadmap/amex-approved-informe-20261013-spec.md`. Sweep unchanged because no historical sweep inventory is revised. | Approved candidate; implementation validation, commit, merge, migration, deployment and Finance UAT remain pending. |

| 2026-10-13 | Product and Engineering canons | Human-approved statement workflow and explicit beneficiary attention policy. Exact amendment text approved with technical specification; evidence: `docs/roadmap/amex-statement-workflow-20261013-story.md` and `docs/roadmap/amex-statement-workflow-20261013-spec.md`. Sweep unchanged: historical inventory is not rewritten. | Approved implementation scope; Git publication, owner-run migration, deployment and Finance UAT pending separate receipts. |

| 2026-10-15 | Product §9.1 and Engineering §8.2.1 | Explicit user approval of the exact 2026-10-14 direct AMEX source contract. Evidence: `docs/roadmap/amex-sat-budget-direct-20261015-spec.md`; typed statements, fiscal context and budget assignment without INFORME, immutable direct cuts and no duplicate recognition. | Approved build scope only; migration, publication, deployment and Finance UAT pending. |

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

2026-10-20: enmiendas exactas de producto/ingeniería revisadas y aprobadas por el usuario en conversación junto con la especificación AMEX de sugerencias/cancelación de cuenta de gastos. Hashes actualizados; sweep unchanged por ser inventario histórico sin nueva observación productiva. Implementación y pruebas aisladas autorizadas; sin publicación/migración/deploy/UAT implícitos. Evidencia: `amex-suggestions-settlement-20261020-spec.md` §10.

### 2026-10-22 AMEX direct sign-correction design

Engineering canon amendment explicitly approved with the technical specification in conversation. Evidence: `docs/roadmap/amex-sign-correction-20261022-spec.md`. Product and Sweep canons unchanged: no new treatment or historical inventory revision. This records approved design; implementation validation, publication, owner-run migration, data correction and Finance UAT require their own evidence.
