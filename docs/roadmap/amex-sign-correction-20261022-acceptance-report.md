# AMEX sign correction: independent acceptance

Date: 2026-10-22. Approved story/spec: `amex-sign-correction-20261022-story.md` and `amex-sign-correction-20261022-spec.md`. Verification is against the working candidate and disposable PostgreSQL only. No production migration, data correction, Git publication, deployment or Finance UAT is represented by this report.

## Acceptance matrix

| Acceptance criterion | Coverage method | Result | Notes or gaps |
| --- | --- | --- | --- |
| AC1: same statement and 16 immutable original rows | Real owner intake; capture physical row identifiers, signed amounts, profiles, original expense links and JSON before/after; replay original file with both original and effective profiles | PASS | Synthetic CSV reproduces the 16-row economic pattern; private CSV never copied into tests. Production exact UUID/preimage enforcement remains an operator check. |
| AC2: authorized actor, reason, before/after audit | Actual preview/confirm service, real migrated revision/row tables and SQL deferred seals; incomplete revision rejected | PASS | No trigger bypass or mocked SQL. Actor deactivation between preview/apply rejected through owning authority chain. |
| AC3: Google charge selectable; Amazon refund not a charge | Actual ASGI HTTP GET to direct statement, real PostgreSQL projection, parse rendered checkbox IDs | PASS | Google +939.00 remains signed +939.00; Amazon -46.53 remains signed -46.53. Fourteen cargo checkboxes. Authenticated browser UAT remains separate; synthetic identity dependency supplies only test actor. |
| AC4: two old expenses cancelled, fourteen active corrected anchors | Real expense owner creates fourteen distinct anchors; inspect cancellation actor/time/reason on two originals; check no overlap and explicit NULL for effective credits | PASS | SQL attacks reject old-anchor resurrection and effective-anchor amount changes. Historical rows stay stored. |
| AC5: one coherent interpretation through policy/export | After correction, explicit Sin CFDI declarations on fourteen cargos, explicit existing credit exclusion on two abonos, real classification/CP assignment, owner preview/confirm, CSV/XLSX/ZIP export | PASS | Tests make explicit Finance decisions; repair itself does not make them. Exclusions in synthetic test are not approval to exclude real production refunds. Expected policy has fourteen consumptions, one journal, balanced debit/credit 8,747.10. |
| AC6: deterministic preview, confirmation, versions, idempotency, atomicity | No-write preview; invalid preview hash rejection; exact replay; changed-payload rejection; identical-key and distinct-key concurrent apply; correction against real classification/manual CFDI-link owners; injected ninth creation failure after Amazon original cancellation | PASS | Fault rollback checks both expense cancellation and partial expense creation plus all revision/row evidence. |
| AC7: incompatible dependencies fail closed | Real classification, Sin CFDI declaration and credit-exclusion owners applied after preview; confirm rejected with no correction effects; inactive actor rejection; actual CFDI/CP/cut state blocks a fresh correction | PASS | CFDI-link and classification races execute their real decision owners under the canonical statement lock. Existing-cut negative executes real SAT-observed CFDI matching, CP and cut. Payment and every polymorphic owner are additionally inspected through service dependency enumeration and builder/schema tests; this file does not claim exhaustive owner enumeration. |
| AC8: backup/preimage and controlled production application | Required manual operational verification | Pending production phase | Not performed by tests. Validate backup restorability, exact source/preimage, actor, release/schema compatibility and post-apply 16-row totals. After evidence is used, destructive rollback/downgrade remains prohibited. |
| AC9: no trigger bypass, no rewritten hashes, no duplicate active import | Actual owner SQL migrations and guards; append-only attacks on both correction tables and physical source; replay yields same import and sixteen total expense rows | PASS | Disposable owner bootstrap is isolated setup; runtime preview/correction does not perform DDL. |
| AC10: correction/repetition/concurrency/rejections before promotion | Fourteen independent PostgreSQL cases | PASS | Production remains untouched. Independent read-only implementation validation and final human review are separate gates. |

## Scope and commands

Changed acceptance files:

- `tests/integration/test_amex_direct_sign_correction_postgres.py`: independent acceptance cases.
- `tests/acceptance/test_amex_sat_budget_direct_postgres.py`: append the actual sign owner migration last to existing isolated fixture bootstrap (delegated by root).
- This report.

Disposable database: `127.0.0.1:55471`, `test_user`, `devnous_test`; each fixture creates/drops its own random schema, applies the owner migrations, and retains all live SQL guards. This is separate from production and any other local multiempresa database. No dependencies installed.

```sh
TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test \
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps \
COVERAGE_FILE=/tmp/amex-sign-acceptance.coverage \
/root/samchat/.venv/bin/python -m pytest \
  tests/integration/test_amex_direct_sign_correction_postgres.py -q --tb=short \
  --cov=devnous.gastos --cov-report=json:/tmp/amex-sign-acceptance-coverage.json \
  --cov-report= --cov-fail-under=0
```

Formatting/unused import checks use the existing release venv (the local pytest venv lacks those tools): `black --check`, `isort --check-only`, `flake8 --select=F` on the new acceptance test file. All three passed. Global lint/type coverage is not claimed by these scoped checks.

## Red evidence and attribution

Independent testing exposed and builders corrected:

1. Composite foreign-key catalog enumeration treated `workflow_mode` as a UUID identity, preventing the preview query. Corrected by pairing source/target FK attributes and restricting target identity to `id`.
2. The corrected cut integrity guard checked an invented snapshot key `imported_expense_id` instead of canonical `expense_id`, blocking the final accounting cut. Fixed by schema owner.
3. Mid-development concurrent SQL/contract edits invalidated exact admission in a test run; these were development synchronization failures. Final verification runs on frozen DDL/contract.

Two acceptance harness assertions were corrected without weakening accounting checks: PostgreSQL legacy Float totals are compared using SQL numeric sums, and inactive actor is expected to raise the owner's `DirectAccessError` instead of `DirectConflict`.

## Final results

Frozen final run: **11 passed**, 593 inherited SQLAlchemy/datetime deprecation warnings, **56.55 seconds**, exit 0. Three additional bounded real-owner tests passed separately: **3 passed**, 11 deselected, 135 inherited deprecation warnings, **32.88 seconds**, exit 0. These cover correction races with classification/CFDI-link and rejection of an existing canonical cut. Coverage for these additions: `/tmp/amex-sign-acceptance-extra.coverage` and matching JSON. Coverage data is supplied for root's combined changed-line assessment; no repository-wide coverage percentage is inferred from this focused suite. Production backup/application verification and real Finance UAT remain pending.

Disposable PostgreSQL cleanup: exclusive cluster on port 55471 was stopped with `pg_ctl -m fast`; `pg_isready` returned no response and its postmaster PID file is absent. Port 55434 and production were untouched.
