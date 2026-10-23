# Independent acceptance — AMEX account treatments

Date: 2026-10-23. **PASS: 15 tests, zero failures and zero skips, 92.50 seconds.** The final run contains 468 inherited warnings. This is technical acceptance against isolated PostgreSQL; productive deployment, authenticated Finance UAT and business acceptance are separate.

The verifier read the approved story/spec, including the owner-installed policy design and physical-project compatibility addenda. The three canons were read in order and their hashes matched the current register. This verifier authored only `tests/integration/test_amex_direct_account_treatment_acceptance_postgres.py` and this report; no implementation, migration, production configuration or business record was changed by this verification.

## Environment and method

Final compatibility rerun: actual PostgreSQL 15.19 on the separately owned disposable SCRAM cluster `127.0.0.1:55471`, database `devnous_test`, user `test_user`, a fresh `direct_acceptance_*` schema for each case. The fixture installs the complete actual owner-migration chain through account treatments. It constructs synthetic bank, CFDI, account, actor and budget identities. The external SAT transport is substituted; one authority regression additionally returns an explicit delegated action grant from permission resolution. Treatment, CP, fiscal, recognition, journal, cut and export owners remain real. The explicit fixture-owner policy installer uses the same transactional DDL boundary as the approved architecture; it does not enable runtime DML or rely on an arbitrary GUC.

The final compatibility rerun fingerprints twenty-one enumerated source/schema/fixture/spec/test files; all twenty-one remained identical throughout its fifteen-case execution. Binding receipt: `/tmp/amex-treatment-independent-sa20-pg15-source-20261023.json`. The earlier fifteen-case receipt had twenty-one total entries and one concurrent route change (twenty stable); its summary count was corrected without changing any recorded hashes. No production credentials, live SAT requests, real-user browser session or runtime startup DDL were used.

## Acceptance criterion mapping

| Acceptance criterion | Coverage method | Result | Notes or gaps |
|---|---|---|---|
| 1. Prestación can reach CP without prior project/category, but cannot post without assignment | `test_acceptance_benefit_send_needs_no_project_but_only_CP_can_assign`; mixed journey | PASS | Real configured CP actor assigns the actual line; ordinary Finance is denied. The Sin CFDI benefit then produces the exact expense/card cut. |
| 2. Actual budget identity is retained without fabricating an operational project | Same benefit case, mixed journey and two-benefit batch | PASS | Item tournament/project stay NULL; the core NOT NULL `ExpenseReport.proyecto` represents absence by `''`. Mapping comes from the real line, and its original budget scope is preserved independently. |
| 3. New partner receivable identifies an active scoped account and bypasses CP/reimbursement | Corrected-sign journey; mixed route; `test_acceptance_partner_WITH_CFDi_posts_only_full_asset_and_card` | PASS | With CFDI: exactly two full-amount debtor/card movements, no expense/IVA posting or budget assignment. Without CFDI: explicit typed declaration, no budget spend, same economic rule. No expense report/document or reimbursement is fabricated. |
| 4. Existing project route retains its category, edition, CP mapping and controls | Three-destination mixed journey | PASS | The existing project classification owner and configured CP owner are used; new treatment does not skip them. |
| 5. Fiscal linking remains independent; Sin CFDI is explicit and typed; prior evidence remains intact | Mixed CFDI benefit, partner WITH CFDI, explicit Sin CFDI cases, grant-only configuration upgrade | PASS | Exact context, linked CFDI/status/proof and original audit payloads survive grant upgrade and treatment. Non-grant identity/scope changes fail closed. |
| 6. Mixed state has one complete immutable policy with exact monetary output | Mixed journey and two-benefit batch/replay | PASS | Mixed case: 448.00 expense + 16.00 IVA + 232.00 debtor = 696.00 card. One policy, three consumptions, two budget assignments, one COI header and one FIN_PARTIDAS closure in both CSV and XLSX. Monetary columns, RFCs and frozen export stability are checked. |
| 7. Accounts must be valid, active and explicitly authorized | Parameterized invalid-account cases | PASS (3 cases) | Ungranted account, inactive account and invalid code type all reject without a treatment decision, journal or expense-account mutation. |
| 8. Preview/confirmation/version binding and batch atomicity/idempotency | Late-stale two-item batch; same/different-key races; successful two-benefit batch/replay | PASS | Separate real concurrent transactions produce one destination. A stale entire batch leaves no partial update. A valid two-item batch creates exactly two decisions, replays without duplication and ends in one 348.00 policy. |
| 9. Route withdrawal preserves history and does not grant new authority; recognized sources are immutable | Removed-account policy/revocation; withdrawn-benefit cleanup; frozen mixed cut | PASS | Removed grant blocks new posting/send/assignment. Current CP can see and return only its existing assignment; non-CP Finance remains denied. Explicit fiscal/destination revocations retain old decisions, policies and the exact budget-return receipt. After cut, the destination cannot be revoked. |
| 10. Original CSV, receipts, corrected signs, fiscal evidence and history remain unchanged | Stable-evidence snapshots; grant upgrade; corrected-sign journey | PASS | The actual sign-correction owner followed by new treatments keeps Google 939 as a charge, Amazon 46.53 as a credit, original physical evidence intact, and a single 1,055.00 charge-side policy. |
| 11. Queue/projection/actuals reflect treatment and count budget once | Mixed actuals, benefit queue and withdrawn-benefit queue | PASS for real financial projections | Debtor is excluded from budget actuals; the mixed budget base is exactly 448.00 from two movements. Benefit's CP row is visible before assignment and remains recoverable after grant withdrawal. Parent-owned HTTP/browser checks are separate evidence, not real-user UAT. |
| 12. New financial review rejects delegated non-Finance early while legacy project delegation survives | `test_acceptance_delegated_legacy_classifier_cannot_review_financial_treatment` | PASS | Configured actor demoted to usuario with explicit action grants cannot obtain options/preview/new confirm/exact replay: all raise finance_actor_required before writes. A separate untouched legacy project item classifies through its existing owner and real scope. Only permission resolution is substituted. |

## Exact financial witnesses

- Mixed state: benefit with CFDI 116.00 (100.00 expense and 16.00 IVA), personal charge without CFDI 232.00 (full debtor asset), project expense without CFDI 348.00. Accounting has exactly one policy and 696.00 on each side. Budget actuals contain only benefit's 100.00 base and project's 348.00; the debtor's 232.00 is absent.
- Partner with CFDI: full 116.00 debtor debit and 116.00 card credit, no expense or IVA movement, no budget assignment, no invented Sin CFDI decision, linked invoice and its frozen export block retained.
- Benefit without CFDI: cannot post before configured CP assignment; after assignment posts exactly 116.00 expense/card, without tax movements or operational project.
- Corrected source: original wrong sign profile is corrected through its real owner; Google 939.00 becomes a debtor charge, benefit 116.00 is budgeted, Amazon refund remains a credit with no expense anchor. All original file/header/receipt/physical source evidence remains identical.
- Successful two-benefit batch: one reviewed confirmation, idempotent replay, exactly two destination decisions, two CP assignments and two consumptions, one 348.00 policy, no operational projects.

## Commands and retained evidence

Final complete command:

```bash
TEST_AMEX_DATABASE_URL=<isolated SCRAM fixture URL> \
COVERAGE_FILE=/tmp/amex-treatment-independent-sa20-pg15-20261023.coverage \
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps \
/root/samchat/.venv/bin/python -m pytest -q \
  tests/integration/test_amex_direct_account_treatment_acceptance_postgres.py \
  --cov-config=.github/coverage-repository.ini --cov=src \
  --cov-report=json:/tmp/amex-treatment-independent-sa20-pg15-coverage-20261023.json \
  --cov-report= --cov-fail-under=0 --disable-warnings --tb=short
```

Result: **15 passed in 92.50 s; 468 inherited warnings; no failures/skips.** Final compatibility log: `/tmp/amex-treatment-independent-sa20-pg15-20261023.log`. Earlier green log retained: `/tmp/amex-treatment-independent-15-verified-20261023.log`. The raw coverage database/JSON are available for combination with unit and other PostgreSQL coverage; this acceptance-only slice is not presented as a standalone changed-code percentage gate.

Scoped Black, isort and Flake8 F checks passed on the new acceptance module. The source-binding receipt and final log preserve the tested object. Broader regression, changed-code coverage, required CI, read-only implementation validation and promotion remain the parent's responsibilities.

## Failures found and attribution

Failures were retained rather than hidden or converted into skips:

1. Initial 10-case run: 8 passed / 2 failed in 23.00 s. Harness precision issues: logical no-project is a blank in a core NOT NULL column, and policy drift may be surfaced as source-changed. Tests now verify exact logical absence and either accurate fail-closed policy/source reason; no financial or authority requirement was removed.
2. A real integration defect then reached the old strict Sin CFDI seal: account-treatment metadata overwrote fiscal `raw_row_json.treatment`. The accounting owner retained that fiscal key and used `account_treatment_kind` for the new classification. Existing SQL financial guards were not weakened. A separate schema-admission error occurred while SQL/contract constants were being edited between fixture creation and runtime admission.
3. A subsequent 12-case run: 9 passed / 3 failed in 31.04 s. Its mixed cut succeeded; a verifier accumulator then needed exact Decimal conversion of legacy Float column decimal strings. Actual implementation failures remained in linked-partner frozen `cfdi_id` proof and grant-withdrawal destination-revocation authority/ancestry. The accounting/schema owners fixed the required source proof and bounded cleanup; this verifier did not patch implementation.
4. The first 14-case run: 13 passed / 1 failed in 38.60 s. The configured CP return hit a premature query-driven autoflush, exposing an incomplete intermediate cleanup to the strict SQL guard. Root moved the required expense read before item mutations so all binding fields are cleared together. SQL was not relaxed. Targeted real-PG withdrawal verification then passed in 4.90 s before the final full run.
5. A leaked session in another synthetic worker once held the shared advisory lock and blocked cleanup. Read-only `pg_stat_activity` identified the blocker; the parent/cluster owner handled cleanup and fixture lifecycle repair. This verifier did not terminate connections or touch production/unrelated clusters.

Original logs remain `/tmp/amex-treatment-independent-acceptance-20261023.log`, `/tmp/amex-treatment-independent-second-20261023.log`, `/tmp/amex-treatment-independent-full-20261023.log`, `/tmp/amex-treatment-independent-final-20261023.log`, and `/tmp/amex-treatment-independent-withdrawal-fixed-20261023.log`. Green evidence is a later separate frozen execution.

## Explicit limits

These cases use synthetic identities and recorded synthetic SAT responses on real isolated owner SQL, not production financial data or real SAT validity. The tests do not claim authenticated Finance/browser UAT, external COI import acceptance, a measured 100-charge performance guarantee or production activation. Schema-only forged-DML/guard-disabling attacks, policy CLI owner admission/rollback and the complete legacy regression are covered by the separately owned suites; this report does not double-count them as its own fifteen cases. The isolated cluster remains under the schema owner's lifecycle control for the remaining validations.


Final object recheck: after scoped typing/defensive hardening, the complete fourteen-case suite passed again in **88.39 s**, 438 inherited warnings, zero skips/failures. During that run the only subsequent source edit was `benefit_expense.budget_concept_id = None` → `cast(Any, benefit_expense).budget_concept_id = None`. Inverting exactly that line reproduced the entire file's original pre-run SHA-256; no value or control-flow change was hidden. Proof: `/tmp/amex-treatment-independent-type-only-drift-20261023.json`. The affected withdrawn-benefit path passed once more against the exact current source in **4.80 s**, and all twenty fingerprints remained stable during that targeted run. Evidence: `/tmp/amex-treatment-independent-final-cast-target-20261023.log`.

The first 88.60 s full green log/source binding remain archived as `/tmp/amex-treatment-independent-pre-typing-verified-20261023.log` and `/tmp/amex-treatment-independent-source-pre-typing-verified-20261023.json`. No financial assertions or SQL guards were weakened to obtain the final result.


Final authority addendum verification: fifteen cases passed against frozen financial services/SQL, including new financial options, preview, confirmation and replay denial for a delegated non-Finance actor. The initial role-regression harness attempted legacy classification on an explicitly financially reviewed item after demoting its decision actor; existing SQL correctly rejected that stale financial authority. The corrected harness uses a separate genuinely legacy item with no treatment decision and preserves strict decision guards. The targeted case passed in 4.06 s before the complete 92.75 s green execution. HTTP route changes during that execution remain subject to the separately owned UI suite, not attributed to this financial acceptance.

The parent identified the only concurrent route edit as CP selection eligibility: partner/unclassified/already-assigned rows are excluded from the CP subset, while fiscal/treatment selection retains all selected rows. This change does not modify financial services or SQL; its desktop/mobile HTTP/browser verification is separately owned by the parent.


## CI compatibility regression, 2026-10-23

PR #500 CI retained two genuine failures (628 cases passed): account-grant withdrawal and benefit-grant withdrawal failed during explicit Sin CFDI revocation. CI used SQLAlchemy 2.1.6; the earlier local environment used 2.0.52. In 2.1 the Core source-view query in `record_event` autoflushed the pending pointer/version transition before its source hash was calculated, followed by a hash-only UPDATE that correctly failed the stale-policy guard. This is an application compatibility defect, not a PostgreSQL/timezone assumption or a weakened acceptance requirement.

The parent changed only the source lookup inside `record_event` to use `session.no_autoflush`; the explicit flush now carries the complete transition and hash together. No SQL guard, migration or contract was relaxed. The schema verifier reproduced both original failures on SQLAlchemy 2.1.6/PostgreSQL 15.19, then passed its full 42-case matrix (15 acceptance + 17 SQL guards + 10 accounting cases) after the fix in 90.63 s; that evidence is separately owned. This verifier independently reran all fifteen acceptance cases on SQLAlchemy 2.0.52/PostgreSQL 15.19: **15 passed, zero failures/skips, 92.50 s**, all twenty-one enumerated fingerprints unchanged.

Current-source coverage rejects all stale line mappings for `amex_direct_common.py` and uses this final execution for its changed statements. Relative to the explicit original base `d591cc4a13987d33d2336bcf872401f3a6c7acb6`, the combined aggregate is **917 / 1,047 changed executable lines = 87.58%, PASS >=85%**. New root-level owner CLI is included; individual files below 85% remain disclosed. Receipt: `/tmp/amex-treatment-combined-changed-coverage-20261023.json`, readable per-file uncovered lines: `/tmp/amex-treatment-combined-changed-coverage-20261023.md`. This verifier changed no implementation or production data. CI itself must still pass again before promotion.
