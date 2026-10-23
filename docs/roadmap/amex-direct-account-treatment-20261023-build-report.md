# AMEX account treatments — build report

Date: 2026-10-23. Base: `d591cc4a13987d33d2336bcf872401f3a6c7acb6`. Approved story/specification and exact canon paragraphs remain current. The user explicitly extended execution authorization through commit, push, PR, merge, migration, production configuration and deployment; no repeated approval checkpoint is required for this scope.

## Completed behavior

- Explicit, audited decisions for project expense, company benefit and partner receivable; homogeneous batches use exact source versions, preview and confirmation.
- Benefits do not require an operational project/category. Juan Pablo must assign an authorized real budget line; its mapping determines the expense account. Core NOT NULL project storage uses the existing empty-string representation.
- Receivables require an authorized active canonical partner account and reason. They bypass budget expense and post full bank amount to debtor/card, preserving linked CFDI or explicit typed NoCFDI evidence.
- Mixed statements retain one complete cut/policy, immutable snapshots, original fiscal evidence and CSV/XLSX export. Legacy project classification, reports, sign corrections, source recovery, settlement and payment behavior remain preserved.
- Owner-installed immutable policy anchors permit only the three additive catalog grant keys without resetting fiscal contexts or clearing CFDI links. New work requires the installed current policy. Grant withdrawal allows only explicit audited revocation/return of original assignments.
- UI separates treatment from optional project classification, previews selected rows and preserves failed input. CP submission includes only eligible saved benefit/project rows and explains omitted selections. CP labels include the actual budget scope/year to distinguish equal concept names.

## Files changed

Source: AMEX models/reexports and schema policy; new treatment owner; existing common, context, effective source, NoCFDI, budget, accounting, shared export and settlement owners; budget actuals owner; existing direct routes.

Operations: owner migration and guarded empty rollback; exact schema contract; owner-only private-manifest policy configuration CLI. No dependency or background job added.

Tests: treatment owner/CLI/routes/posting units; real PostgreSQL schema/attack/race/accounting/acceptance; browser journeys; existing fixture migration chains and schema admission assertions. Docs: approved story/specification, independent acceptance/validation reports, this report, generated route inventory and the two explicitly approved canon additions with register hashes. The codebase sweep is unchanged because its cross-module diagnosis did not change.

## Verification and commands

All commands ran from the isolated worktree using existing virtual environments. Test dependencies were not added to production requirements.

| Verification | Result | Evidence |
|---|---|---|
| Integrated AMEX/HWC/COI unit regression, `pytest` scoped modules | 1,209 passed | `/tmp/amex-treatment-integrated-unit-final-20261023.log` |
| HTTP routes, separate process | 178 passed | `/tmp/amex-treatment-http-final-20261023.log` |
| Chromium desktop/mobile treatment/legacy journeys | 15 passed | `/tmp/amex-treatment-browser-final-20261023.log` |
| Legacy NoCFDI browser with real PostgreSQL | 1 passed | `/tmp/amex-treatment-no-cfdi-browser-final-20261023.log` |
| Independent financial acceptance through policy/CSV/XLSX | 15 passed, zero skipped | `/tmp/amex-treatment-independent-15-verified-20261023.log` |
| Schema/attack/race/rollback ownership | 17 passed | `/tmp/amex-treatment-schema-race-final-20261023.log` |
| Historical PostgreSQL regression | 101 passed, 1 existing explicit skip | `/tmp/amex-treatment-historical-final-20261023.log` |
| Broad PostgreSQL regression, `pytest tests/integration/test_amex*.py` | 531 passed, 2 preexisting locale-dependent failures, 1 existing explicit skip | `/tmp/amex-treatment-root-pg-final-20261023.log` |
| Exact failing suggestions module on unchanged base | Same 2 failed / 14 passed | `/tmp/amex-treatment-suggestions-baseline-20261023.log` |
| Same 16 assertions/ranking source on isolated C.utf8 database | 16 passed | `/tmp/amex-treatment-suggestions-locale-attribution-20261023.json` |
| Generated inventory, final post-format test | 9 passed; 609 routes, 44 canonical actions, zero parse gaps | `/tmp/amex-treatment-inventory-postformat-20261023.log` |
| Aggregate changed executable coverage (includes new CLI) | 917/1,047 = **87.58%** after the compatibility correction, passes 85%; per-file gaps disclosed | `/tmp/amex-treatment-combined-changed-coverage-20261023.json` |
| Final exact-source focused coverage refresh | 277 passed | `/tmp/amex-treatment-combined-changed-coverage-20261023.md` |
| Five new/changed backend owners, scoped mypy | Passed | Builder and coordinator evidence |
| Workflow/runtime packaging/registration/accepted regressions/compile/diff hygiene | Passed | Existing repository check scripts and `compileall` |

Commands include existing `scripts/ci/check-pr-quality-gate.py`, `scripts/check_runtime_packaging_consistency.py`, `scripts/ci/check-registration-operational-surface.py`, `scripts/ci/check-accepted-regressions.py`, `scripts/private_plugin/inventory.py`, targeted pytest/coverage, Black/isort, Flake8 F, mypy and `git diff --check`. Financial runs overlap; their counts must not be summed as unique tests. The 15-case acceptance report records stable source fingerprints and separate UI verification.

## Known failures and corrections

The two broad PostgreSQL failures are unchanged suggestion SQL/Python accent similarity cases. They reproduce on base d591. The disposable original database has UTF8 encoding but locale C, where SQL lower leaves uppercase accented Ó unchanged; an isolated C.utf8 database passes all 16 module cases. Only the temporary fixture's allowed test port changed for that proof; ranking source and assertions are byte-identical to base. No search implementation or test skip was changed in this feature.

Scoped Flake8 F reports six existing findings: three in models and three in the budget owner. Model/budget import formatting debt is retained rather than rewriting unrelated source; new/other changed Python passes scoped Black/isort. Router mypy has ten unchanged baseline errors; five scoped backend owners pass. These findings are not described as a globally clean lint/typecheck.

An attempted combined synchronous Playwright and async HTTP invocation failed due to an active event loop. Separate final browser and HTTP processes passed; the unsuccessful runner log is retained. Earlier change-caused SQL, date, physical project-null, fiscal metadata, CFDI cut member, permission and UI selection defects were corrected and reverified. No failing assertion was hidden or relaxed.

## Validation, deviations and release limits

Independent source/SQL/scope validation is favorable with no unresolved source finding. Its two important findings (Finance preview authority and CP selection of receivables) were corrected within approved scope and verified. Implementation specification sections 12–15 document the necessary anchored policy compatibility, physical project representation, withdrawal cleanup and consistent Finance authority; business scope did not change.

No production rows have been reclassified, no real policy generated as QA and no financial links changed by these tests. Local synthetic/real-PG verification is not authenticated Finance UAT or an external COI import. Production migration/configuration/deployment and GitHub required checks need their own receipts. Configuration requires actual explicit benefit catalog selection and a real designated audit operator; no audit identity or catalog choice will be invented. Rollback refuses to remove committed policy/decision evidence; after use, preserve compatible code and forward-fix.

## CI compatibility correction before publication

The first PR #500 CI run passed **6,139 unit tests** (8 existing skips) and **103 browser tests**, but integration correctly blocked the merge: **628 passed, 2 failed, 6 existing skips**. These two failures were caused by this feature, not attributed to an accepted baseline. Both concerned explicit cleanup after grant withdrawal.

The CI environment resolved SQLAlchemy 2.1.6, whereas the local environment used 2.0.52. SQLAlchemy 2.1 autoflushes before Core SELECTs as documented in its [official migration notes](https://docs.sqlalchemy.org/en/21/changelog/migration_21.html). `record_event` queried the effective source after changing the pointer/status/version; that intermediate autoflush split cleanup from its source hash. The strict SQL guard correctly rejected the resulting standalone hash update.

The correction uses the existing `session.no_autoflush` mechanism only around that source SELECT. Hash computation and the explicit flush remain together. Source recovery/sign revisions are explicitly flushed by their owners before this point. No SQL guard, migration, catalog, permission, dependency or business rule changed. Canon unchanged for this correction: it restores the already approved atomic transition and source-proof contract.

Actual PostgreSQL 15.19 with SQLAlchemy 2.1.6 reproduced both original failures (**2 failed, 13 deselected**) before the correction. Afterward **42 tests passed in 90.63s**: all 15 financial acceptance cases, 17 schema/security cases and 10 accounting cases. Arbitrary stale source/budget writes and unauthorized actions remain denied. Related unit regression after correction: **1,325 passed in 16.45s**. Scoped Black/isort/Flake8 F and diff hygiene passed; regenerated route inventory is unchanged. Logs: `/tmp/amex-treatment-ci-216-pg15-{before,after}-20261023.log` and `/tmp/amex-treatment-ci-fix-units-20261023.log`. Required GitHub checks must rerun on the corrected commit before merge; production has not been changed by the failed CI attempt.

Independent backward-compatibility rerun after that correction: **15/15 passed in 92.50s** on PostgreSQL 15 with SQLAlchemy 2.0.52. Evidence: `/tmp/amex-treatment-independent-sa20-pg15-20261023.log`. This is additional local acceptance, not a claim that the failed first GitHub run passed.
