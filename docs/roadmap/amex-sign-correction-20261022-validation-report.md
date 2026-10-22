# AMEX sign correction: independent read-only validation

Date: 2026-10-22. Phase: read-only review, performed after the acceptance write/test phase. Reviewer authored acceptance tests and reports only, not domain implementation, migration, models, CLI or UI. No implementation fixes were made during this review. Review object: isolated `amex-sign-correction-20261022` candidate, approved story/spec and exact engineering-canon amendment. Reviewed file hashes are recorded in `/tmp/amex-sign-validation-reviewed-files.json` (30 implementation/test/inventory files; reports excluded).

## Verdict and findings

**No unresolved critical, important or minor implementation findings identified in the reviewed candidate.** This is a source/SQL/acceptance review, not authorization for Git publication, production migration, correction or deployment. Root's final build report must retain final regression, coverage, lint/type and release-gate results; this review does not substitute for those results.

An important rollback concurrency defect identified by the root cross-review was repaired before this validation closed: empty rollback now obtains `ACCESS EXCLUSIVE` locks on both sign tables before deciding that evidence is absent. The schema builder's real PostgreSQL witness starts rollback while a complete 16-row confirmation is uncommitted, observes the waiting lock in `pg_locks`, commits confirmation and proves rollback refuses to erase revision 1 / rows 16. The repaired SQL and test were inspected during this review. Empty rollback and fresh forward reinstall are separately exercised by schema tests.

## Scope and business behavior

The implementation follows the approved correction scope. It preserves the same import and original CSV, header, receipts and physical interpretation. Two append-only owners supply corrected effective rows. Opposite profiles are explicit; dates, signed amounts, currency and membership are reproduced from original bytes. SQL CASE resolves a corrected credit expense anchor to NULL; it does not fall back to the superseded physical expense.

The maintenance CLI restricts production use to the exact approved import UUID, `debit_negative` to `debit_positive`, sixteen rows, fourteen debits / two credits, and reviewed totals. Actor, reason, version, preview hash, saved manifest and explicit confirmation are required; there is no default actor or extra runtime endpoint. Domain service reuse remains possible only through the owning financial authority and integrity checks, as the approved specification allows. No merchant inference, benefit/receivable redesign, automatic fiscal decision, CFDI creation, credit exclusion, policy creation or business backfill was added by the repair.

Cancellation uses the existing expense status, actor, timestamp and reason plus the approval audit owner. Fourteen new expense anchors are created through `create_expense_from_data`; original amounts and history are preserved. New SQL protects superseded cancellation from resurrection and effective bank economics from mutation. Cancellation side effects are contained in the same savepoint/transaction as revision, rows, events and new anchors.

## Authority, identity, dependencies and atomicity

Preview and confirmation invoke `load_statement` and the existing direct financial authority chain, including active employee, explicit denials, card/entity scope and current fiscal configuration. Confirmation reacquires the statement/state/recognition locks and original expenses in deterministic order. It recalculates the full plan and checks the bound preview hash and source version before creating evidence. Exact replay returns the durable revision; another key or changed payload cannot append another correction.

Dependency checks fail closed on physical decision fields and original expense fields, actual FK owners (correctly paired composite FK attributes), existing audit events, accounting JSON references, budget keys and approvals. Recovery/cuts/completion prevent a new correction. No dependency is removed to force eligibility. Real-owner concurrency tests cover classification and CFDI linking against correction; each produces only one persisted outcome. A real CFDI/CP/accounting cut blocks a fresh sign correction. Other payment/historical dependency owners have focused builder tests and were inspected in the generic dependency enumerator; this is not a claim that every possible future polymorphic owner can be discovered automatically.

The original-state and corrected-state records, expense changes, final source versions and per-row events must pass SQL seals at commit. Membership, sign/profile consistency, effective source digest, totals, exact source file, fresh expense anchors and cancellation audit are checked. The independent injected failure after an original expense cancellation and multiple new anchor creations rolls all effects back. Both identical-key and distinct-key concurrent confirmations are tested against real PostgreSQL.

## Consumer, accounting and immutability review

Matching, budget, historical settlement/prior, Sin CFDI and accounting consumers continue to resolve the shared effective source relation. Changes in the source/item, fiscal-match and no-CFDI hashes include revision/row/proof evidence while preserving existing hashes for uncorrected states. UI rows expose effective amounts, kind and profile while the small audit notice preserves the original declaration. The actual HTTP response shows Google selectable and Amazon outside cargo selection.

The complete previous `amex_direct_check_cut` function was compared with the new migration function. Its body differs by one additional corrected-source proof guard only. All previous fiscal checks, immutable ownership, economic balancing and `EXCEPT ALL` snapshot/movement multiplicity checks remain intact. The added guard binds canonical cut members to sign revision, sign row, proof hash, effective source JSON and canonical `expense_id`.

Independent acceptance goes beyond loading the correction: fourteen explicit Sin CFDI declarations and two explicit existing credit decisions are followed through real classification, CP assignment, preview, one accounting cut and CSV/XLSX/ZIP export. Fourteen consumptions and one balanced policy of 8,747.10 are persisted, with no duplicate import/expenses. These synthetic follow-on decisions do not authorize them for the real statement. Normal CFDI matching and its race are tested separately using an external SAT transport substitute only.

Revisions, rows and physical sources remain guarded against UPDATE/DELETE. The new tables are excluded from startup/runtime provisioning and admitted only through the exact owner migration contract. Forward DDL contains no business-data backfill. Rollback is limited to an empty layer; after application, compatible forward repair is mandatory. Deployment must coordinate the exact-schema release transition as specified; old release downgrade cannot silently ignore correction evidence.

## Privacy, canon and inventory

No private statement CSV, credentials, RFC payload or production preimage is added to Git. Acceptance uses synthetic rows and test identity. CLI error reporting avoids SQL parameters and never prints original CSV content. Saved preview JSON contains only the reviewed operational manifest and requires private operator storage.

All three canon SHA-256 values were reverified against the convergence register. The engineering amendment is the exact human-approved text; product and sweep canons are unchanged. Generated route inventory/matrix changes only refresh the existing route source hash and line offsets; no extra endpoint or permission surface was introduced. Root reports the nine inventory contract tests pass after regeneration.

## Evidence and remaining phase boundaries

- Independent acceptance: fourteen unique real PostgreSQL cases pass across the frozen eleven-case run (56.55 s) and three additional real-owner race/cut cases (32.88 s). See the acceptance report for exact commands, warnings and coverage receipts.
- Schema builder reports nine real PostgreSQL schema cases pass (15.09 s), including guarded rollback concurrency; source test and repaired SQL inspected here.
- `git diff --check` passes. New acceptance test file passes scoped Black, isort and Flake8 F checks in the existing release venv.
- Root final frozen unit/inventory regression: **1,082 passed in 104.92 s**. Combined final changed-executable coverage: **313/325 = 96.31%**, all changed Python files measured, receipt `/tmp/amex-sign-changed-coverage-20261022.json`. Scoped types/lint are recorded in the build report. Existing models/schema formatting debt remains disclosed; these results do not claim repository-wide clean style/types.
- Production backup/restorability, exact original CSV/preimage, operator identity, owner migration, immutable release, post-apply sixteen-row verification and authenticated Finance UAT remain operational checkpoints. None was performed or asserted by this review.

No deviation from the approved architecture or business scope was found. No production dependency was installed, no runtime startup DDL was added, and no production business data was modified by this verification.

Disposable PostgreSQL cleanup: exclusive cluster on port 55471 was stopped with `pg_ctl -m fast`; `pg_isready` returned no response and its postmaster PID file is absent. Port 55434 and production were untouched.

## PR #497 CI inventory correction review

After publication of candidate commit `c1034c81c`, CI found that the generated inventory recorded 476 scanned Python files while the tracked tree contained 477. The original local regeneration ran before the new sign service became tracked. This is a change-caused generated-metadata defect; the earlier local green result did not prove the post-commit inventory count.

Independent read-only review of the corrective diff confirms that only `docs/private-plugin/route-inventory.json` changes `source_file_count` from 476 to 477. A parsed comparison proves every other JSON field is identical; the route matrix is unchanged. There is no route, permission, authority, source behavior, accounting decision or production change. `git diff --check` passes.

Root reran `PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps /root/samchat/.venv/bin/python -m pytest -q --no-cov tests/unit/private_plugin/test_inventory.py --disable-warnings --tb=short`: **9 passed in 22.65 seconds**. The identified inventory defect is closed locally. Remote required CI gates must pass on the corrective commit before merge; this addendum does not claim they have already rerun. No critical, important or minor findings remain in this corrective diff.
