# Independent implementation validation — AMEX account treatments

Date: 2026-10-23. Result: **SOURCE REVIEW FAVORABLE; no unresolved source finding.** The completed broad PostgreSQL run has two failures reproduced on the unchanged `d591cc4a13987d33d2336bcf872401f3a6c7acb6` baseline; they are pre-existing and are not represented as green. This is a source/SQL/scope review with technical acceptance evidence, not ship readiness or a claim of production activation, authenticated Finance UAT or successful external COI import. Release-wide gates remain the coordinator's responsibility.

Reviewed object: `feat/amex-account-treatments-20261023`, against base `d591cc4a13987d33d2336bcf872401f3a6c7acb6`, including new files and the final authority/UI corrections. Final reviewed SHA-256: router `1d4fdc16e7659dfa40717647e6edfc326318d068090142607289e2b2aeb76c4f`; treatment owner `743f50f468d783498413af1ab7a4f2130c18a84861f5983dd0f509c0ee434f83`. The validator changed only this report. No source, tests, migrations, production configuration or business data were changed by this validator.

## Critical findings

None discovered.

## Important findings

None remain open. Two issues found during review were returned to the coordinator and corrected within the approved scope:

1. **Financial preview/SQL authority mismatch, corrected.** The new treatment options/preview/confirmation previously inherited delegated legacy `clasificar` authorization, whereas the decision INSERT guard requires an active Finance role. An ordinary configured actor with an explicit classification grant could receive a valid preview and fail at confirmation. All three new entrypoints now request `finance=True` in `amex_direct_account_treatment_service.py:368`, `:435`, and `:726`, including the replay path. Legacy project classification remains delegated as before. The independent PostgreSQL case `test_acceptance_delegated_legacy_classifier_cannot_review_financial_treatment` verifies denied options/preview/confirm/replay and successful legacy classification. No SQL role or business permission was widened; specification §15 records this consistency correction.

2. **Partner selections offered CP submission, corrected.** The shared selection script originally copied partner receivables into the CP form. The backend rejected them, but the UI contradicted specification §8 and could block a mixed selection of benefits and personal charges. `admin_amex_direct_routes.py:1060` now records eligibility from the saved treatment and classified stage. The CP form starts disabled; its selection contains only eligible benefits/projects, while treatment/fiscal selections remain complete. A visible eligible/omitted count explains the operation. Browser tests cover partner-only disabled submission and mixed submission of the exact eligible ID/versions on desktop and mobile. Backend rejection of forged/incompatible submissions remains intact.

## Minor findings

None discovered requiring a source change. Historical typing/lint debt and the combined test-runner failure are recorded below rather than represented as clean required checks.

## Scope and invariant assessment

The implementation matches the approved story and specification, including §§12–15. Source changes remain within the treatment owner, existing AMEX/budget/accounting owners, schema/models/migration, UI, generated route metadata, tests and approved documentation/canon scope. No dependency, background task, parser/sign change, SAT ranking, PASE automation, payment or historical reclassification was introduced.

The ordered canon bootstrap and register hashes passed: product `4f369621a03290d320c9b9efbd5f16683bcfb6e50d99bc5bae1e3d8e7d45345e`, engineering `19f670adb418ac2f359f793cde5f32d351686dd2fa6874ddc7ad601dc0f7305e`, sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`. The two canon additions are the approved treatment paragraphs with dated evidence/register updates; the sweep remains unchanged.

Reviewed boundaries include:

- Owner-installed immutable policies anchor grants; application DML cannot self-certify supplied JSON. Compatibility removes only the three additive grant keys and preserves the old fiscal, identity and authority fields. New actions/cuts require the current installed policy.
- Decisions are append-only, explicitly confirmed, version/source-bound and idempotent. Import/global-recognition/item locks, SQL transition/seal guards and complete cut membership preserve atomicity and uniqueness. Legacy sources without a decision retain their original contract/hash.
- Benefits retain actual CP assignment and mapped expense/card accounts without inventing an operational project/category. Core `proyecto=''` implements the existing NOT NULL column's approved absence representation. Partner receivables require an active allowed debtor account, bypass CP/expense actuals, retain linked CFDI where present and post only full debtor/card movements.
- Explicit typed Sin CFDI evidence remains separate from account classification. Frozen snapshots preserve fiscal metadata, linked identity, full movement multisets and exports. Existing fiscal/status/shared-recognition controls and historical source selection protections remain active.
- Withdrawn grants permit only bounded original fiscal/treatment revocation and CP return with original assignment/policy proof and current authority; they do not authorize new assignments or posting. SQL preserves source immutability and requires exact audit events.
- Policy CLI validates private manifests, exact config/catalogue/operator/hash and replay; actor lock precedes the exclusive policy lock. Temporary removal and exact restoration of only the DML guard occur in one transaction; TRUNCATE protection stays installed. Empty rollback locks before checking policies/decisions and refuses populated evidence. A read-only comparison confirmed its restored cut-item, no-CFDI and cut-check function bodies match the previous owner migrations exactly after whitespace/CREATE normalization.
- Forms retain session/CSRF, escaping, explicit confirmation and current preview/version requirements. No nested form or new unauthenticated write endpoint was introduced. The new path does not bypass shared financial owners.
- Follow-up label review: the benefit-only budget-option label now prefixes the actual selected row's `tournament_name` and version `edition_year` before concept/version/status. Both values already come from the existing `l.*`/version read query (`budget_lines.tournament_name` is NOT NULL). Grant predicates, IDs, account mapping, ordering and writes are unchanged; the original project-route label is unchanged. This meets specification §8 disambiguation without inventing an expense project.

## Verification evidence

This validator ran the two scoped owner/router unit modules twice. The final run was **44 passed, 1 warning, 4.21 s**, using `PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python -m pytest -q tests/unit/gastos/test_amex_direct_account_treatment_service.py tests/unit/gastos/test_amex_account_treatment_routes.py --disable-warnings --tb=short`. `git diff --check` also passed. Read-only searches, diff inspection, SHA checks and the rollback function comparison were performed; a missing unqualified `python` executable and an initial comparison-regex mismatch were corrected before the successful comparison.

The following separately owned evidence was inspected, not represented as runs performed by this validator:

| Check | Observed result | Evidence |
|---|---|---|
| Independent real-PG acceptance, including final authority case | 15 passed, 468 inherited warnings, 92.75 s; zero skips | `/tmp/amex-treatment-independent-15-verified-20261023.log` |
| Financial source binding | Financial sources/schema stable during final acceptance; router changed for the separately tested UI correction | `/tmp/amex-treatment-independent-15-source-verified-20261023.json` |
| Final isolated HTTP suite | 178 passed, 1 warning, 8.77 s | `/tmp/amex-treatment-http-final-20261023.log` |
| Final isolated browser suite, including both new UI regressions | 15 passed, 1 warning, 13.80 s | `/tmp/amex-treatment-browser-final-20261023.log` |
| SQL guard/rollback/race suite | 17 passed, 423 warnings, 28.22 s | `/tmp/amex-treatment-schema-race-final-20261023.log` |
| Accounting/CLI owner regression | 153 passed, 291 warnings, 38.36 s | `/tmp/amex-treatment-accounting-worker-final-tests.log` |
| Historical PostgreSQL regression | 101 passed; one existing explicit receipt-regeneration skip; 177.46 s | `/tmp/amex-treatment-historical-final-20261023.log` |
| Completed broad PostgreSQL/coverage run | 531 passed, 2 failed, 1 existing skip; 12684 warnings; 1093.53 s | `/tmp/amex-treatment-root-pg-final-20261023.log` |
| Unchanged baseline reproduction of the two broad-run failures | Same two failures; 14 passed; 1 warning; 2.53 s | `/tmp/amex-treatment-suggestions-baseline-20261023.log` |
| Broad AMEX/HWC/COI unit regression before the final narrow authority/UI correction | 1209 passed, 40 warnings, 15.33 s | `/tmp/amex-treatment-integrated-unit-final-20261023.log` |
| Existing real NoCFDI browser journey | 1 passed, 37 warnings, 10.51 s | `/tmp/amex-treatment-no-cfdi-browser-final-20261023.log` |

The first combined synchronous-browser/asynchronous-HTTP invocation failed with `Runner.run() cannot be called from a running event loop`: **171 failed / 22 passed**, preserved in `/tmp/amex-treatment-ui-validation-final-20261023.log`. It is attributed to process-level Playwright/asyncio runner interaction; separate fresh-process runs above passed both suites. It was not silently skipped. The coordinator records ten baseline router mypy diagnostics and three pre-existing budget lint diagnostics; those are not claimed as passing global type/lint checks.

## Residual risks

- The broad PostgreSQL run is **not globally green**: `test_sql_python_similarity_parity_missing_dates_and_nonfinite_money` and `test_sql_python_named_issuer_similarity_parity[LA MANSIÓN CDMX-MANSION SA DE CV-Restaurante]` both fail the same SQL/Python similarity comparison on the unchanged baseline. The coordinator's baseline reproduction establishes pre-existing failures, not failures caused by this change. Locale-specific explanation remains under separate investigation; this report does not assert a locale cause without its evidence. Suggestion ranking/parser changes remain outside this approved scope.
- This review does not close any required CI, inventory/release-gate or final combined changed-line coverage calculation that the coordinator has not yet completed. No claim of complete release validation is made from the earlier owner-only coverage receipt.
- Production policy configuration/catalogue scope and operator attribution must use the actual business facts requested by the coordinator; the validator did not infer them, query production or activate configuration. Owner migration, private backup/preimage, promotion, health/route denial and serving SHA checks remain deployment tasks.
- Synthetic PostgreSQL and browser evidence do not establish authenticated Finance UAT, real SAT status, external COI importer acceptance or a measured 100-charge performance guarantee.
- After policy/decision evidence exists, destructive downgrade is intentionally unavailable; recovery requires compatible code/forward correction. Owner DDL authority remains outside the ordinary-DML threat boundary as explicitly specified.

No additional implementation deviation was found. The corrected authority check is documented in §15; physical blank-project and grant-withdrawal behavior are the already documented compatibility/cleanup clarifications, not historical data repair.

## Final local closeout addendum

The source, independent acceptance and local aggregate coverage verdicts are favorable. Required GitHub CI is still pending; this report does not declare deployment or production acceptance completed.

The final coverage receipt `/tmp/amex-treatment-combined-changed-coverage-20261023.md` reports **916/1045 changed executable lines = 87.66%, PASS against the aggregate >=85% gate**, including the new root-level policy CLI. Its current-source refresh passed **277 tests in 119.46 s**. Per-file percentages below 85% are explicitly disclosed rather than presented as individual passes: treatment owner 82.80%, direct budget owner 77.42%, and four changed executable budget-service statements 50%. This aggregate gate closes the previously pending local changed-line calculation; it does not substitute for CI.

The two broad-run failures now have controlled infrastructure attribution: `/tmp/amex-treatment-suggestions-locale-attribution-20261023.json` proves the original disposable UTF8 database uses locale C and SQL lowercasing retains accented uppercase Ó, while C.utf8 lowercases it consistently with Python. A separate disposable C.utf8 cluster passed **all 16 unchanged suggestion assertions in 2.53 s**. Ranking source and assertions are byte-identical to baseline; only the temporary fixture's allowed connection port changed. The original cluster was preserved. Therefore the original 531-pass/2-fail/1-skip result remains disclosed, with the two failures classified as pre-existing infrastructure-sensitive locale behavior; no search/parser patch or skip was introduced.

Final formatting/reexport review found no expanded behavior: the coordinator reports AST-identical literal wrapping of the new schema contract; the validator independently confirmed `models.py` has the exact baseline AST after removing only the two approved treatment model exports. Its final textual change is confined to their existing import block. The label-only budget clarification reviewed above also remains within scope. Final `git diff --check` passed. The build report is present at `docs/roadmap/amex-direct-account-treatment-20261023-build-report.md` and distinguishes these local results from GitHub checks and production receipts.

Lint/type limits remain explicit: six baseline Flake8 F findings (three models and three budget-owner findings), ten unchanged router mypy diagnostics, and existing formatting/import debt are not described as globally green checks. Scoped backend typing, new/other changed formatting and regenerated inventory checks are reported by the coordinator as passing. No unresolved corrective source finding was identified in this closeout; required CI, fact-based production policy configuration, migration/deployment receipts and authenticated UAT remain separate.

## SQLAlchemy compatibility revalidation after first CI

The first PR #500 CI run found **two change-caused revocation failures with 628 passing tests** under SQLAlchemy 2.1.6; these are distinct from the earlier, pre-existing locale failures. The two exact withdrawal cases were reproduced locally before correction against the CI SQLAlchemy version and PostgreSQL 15 (`/tmp/amex-treatment-ci-216-pg15-before-20261023.log`: 2 failed, 13 deselected, 7.65 s). They were not dismissed as baseline debt or skipped.

The coordinator's fix adds only a narrow `with session.no_autoflush` around the existing Core SELECT in `amex_direct_common.record_event`, plus explanatory comments. It leaves the source-version bump, `item_hash`, timestamps, explicit `session.flush()` and audit-event construction unchanged. SQLAlchemy documents that 2.1 added autoflush for Core statement execution, whereas 2.0 did not perform that flush. This block restores the intended atomic update of decision/status/version together with its computed hash; it does not suppress the existing explicit flush, SQL guards or final transaction validation. [Official SQLAlchemy migration guidance](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html#session-autoflush-behavior-simplified-to-be-unconditional).

The validator also traced shared corrected-source paths: both sign-correction and period-recovery owners explicitly flush their new immutable source/proof row before calling `record_event`. Therefore the query still observes the actual correction/recovery evidence while the hash uses current in-memory operational fields. No SQL, migration, schema contract, dependency, account rule, grant or authority boundary changed. Reviewed common-owner SHA-256: `a81ea3622bbdefdf2180f3bb515b4c75810b538197980c62cb770bb26113fbfe`.

Post-fix evidence inspected:

- SQLAlchemy 2.1.6 / PostgreSQL 15: **42 passed, 1180 warnings, 90.63 s**, covering 15 independent acceptance, 17 guard/schema/rollback and 10 accounting/CLI cases, including the exact original failures. Log: `/tmp/amex-treatment-ci-216-pg15-after-20261023.log`.
- Independent SQLAlchemy 2.0.52 / PostgreSQL 15: **15 passed, zero failures/skips, 92.50 s**. Log: `/tmp/amex-treatment-independent-sa20-pg15-20261023.log`.
- Coordinator's unit regression after this fix: **1325 passed, 70 warnings, 16.45 s**. Log: `/tmp/amex-treatment-ci-fix-units-20261023.log`.

Read-only compatibility verdict: **favorable, no unresolved source finding**. The correction stays within the approved behavior and does not require a new business rule or permission. Updated coverage/source-binding acceptance receipts remain the verifier/coordinator's closeout artifacts; GitHub CI must still be rerun successfully before reporting its checks as passed. The earlier green local results alone did not establish compatibility with CI's newer dependency version.

## Integration with concurrent main / PR #499

The coordinator reports the corrected head `c49a0fb97` passed required CI (630 integration, 6139 unit and 103 browser tests, with the coverage gate passing). The inspected integration log confirms **630 passed, 6 skipped, 13439 warnings, 1505.55 s**; it is not represented as zero skips. Main then advanced to `114be344e20a76c74c23ece4ff00cf3012b0d5a9`, merging approved PR #499. This addendum reviews the clean local integration before publication; prior-head CI does not establish green checks for the new merge head.

Read-only byte comparisons against both parents found **all 18 AMEX source/script/migration files exactly equal to `c49a0fb97`**, **all 11 upstream source/script files exactly equal to `114be344`**, and **zero overlapping source paths or source drift**. Canon/register files are unchanged from the already reviewed AMEX head. The only shared integration surface requiring regeneration is generated route inventory/matrix; the current inventory parses and retains the treatment route records. Final regenerated inventory gates remain coordinator-owned.

The upstream project/person authorization changes remain in their approved document owners and require explicit provisional-policy activation. They do not replace AMEX direct Finance role, explicit tool permission, actor/card/fiscal scope, installed treatment-policy grants, CP owner or cut confirmation. The shared AMEX `access_control_service` owner is unchanged; AST comparison confirms the imported shared CFDI-payment validation function in `documento_service` is unchanged. The upstream document workflow still invokes AMEX report posting/final-evidence owners; its legacy PostgreSQL fixture adds empty policy tables to preserve unactivated authority without weakening those financial guards. This merge does not execute the upstream policy CLI, install policy data or repair business records.

The updated current-source coverage receipt now reports **917/1047 = 87.58%, aggregate PASS**, including the compatibility fix and excluding the stale prior common-owner mapping. Independent SQLAlchemy 2.0 acceptance also records all 21 source fingerprints stable. Earlier 87.66% figures in this report describe their earlier object and are superseded by this current receipt.

Integrated source review remains **favorable, no unresolved Critical/Important/Minor finding**. Required CI must run again on the final integrated head; regeneration/CI/deployment receipts and real-user UAT remain separate. No upstream rules, tool or financial data were edited by this validator.
