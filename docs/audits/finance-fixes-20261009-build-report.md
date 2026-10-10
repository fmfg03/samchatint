# Finance fixes: build and review report

Date: 2026-10-09. Base: `d548d148f7521988587d6498b5461901c21a2ee3`.
Candidate: `/tmp/samchat-finance-fixes-20261009`, isolated detached worktree.
This section records the original approved candidate and its local validation.
The final diff was approved and commit/push/PR/merge were subsequently authorized.
Delivery and integration evidence is recorded below; deployment is excluded.

The user approved the seven-point functional scope and then the technical spec
in this conversation. DIOT is excluded and belongs to another assistant.
The primary `/root/samchat` checkout retains its original four tracked modified
files, with the same 463 insertions and 44 deletions observed before this build.

## Completed behavior

1. Budget account assignment resolves an owning document's partida when the
   expense itself lacks one. New regular/quick expenses and supplements apply
   mapping after the informe relationship exists. Explicit account selections
   remain intact. Accounting previews flag missing mappings and discrepancies.
2. Fiscal net follows the existing transferred-minus-retained rule, including
   local taxes. Detailed IVA stays separate from ISH/IEPS. Manual net input
   retains its total calculation but stores unknown IVA as NULL with a visible
   warning. The existing legacy estimation fallback remains, with an explicit
   warning when CFDI transfer detail is missing.
3. Real Operations references appear early in document-owned boards and the
   expense CSV. Existing queries/projections supply lineage; aggregate and
   unrelated entity rows retain native identifiers. The complete inventory is
   in `finance-fixes-20261009-table-inventory.md`.
4. Existing administrative roles enter the personalized `/panel`; employee,
   other-profile and anonymous destinations remain governed by existing guards.
5. Information tables receive sticky headings and independent reachable bottom
   horizontal controls, including dynamically inserted historical-accounting
   tables and cleanup of removed controls. Existing wrappers are reused.
6. Proof confirmation defaults to the recorded cutoff date (`run_date`, or the
   recorded closing date when absent). Accounting can edit one visible date per
   document. Single and batch paths use that date before payment registration,
   audit old/new/default dates and actor, and preserve the closed snapshot.
   Missing cutoff requires an explicitly entered date. Audit failure aborts the
   confirmation transaction. Batch submission includes dates only for selected
   documents. Its pre-existing malformed JavaScript braces were corrected.
7. CxC displays pending links separately, directing review to the existing
   budget approval surface and preserving edition/version context. Only
   approved links enter recognized totals; active links cannot also appear as
   unlinked candidates. No automatic approval or collection was introduced.

## Files changed

Application:

- `copa_telmex_dashboard.py`
- `src/devnous/gastos/routes/admin_routes.py`
- `src/devnous/gastos/routes/user_routes.py`
- `src/devnous/gastos/routes/support_routes.py`
- `src/devnous/gastos/services/budget_concept_account_service.py`
- `src/devnous/gastos/services/expense_accounting_service.py`
- `src/devnous/gastos/services/payment_run_service.py`
- `src/devnous/gastos/services/customer_success_audit.py`
- `src/samchat/ar/service.py`
- `src/samchat/ar/admin_ui.py`
- `src/samchat/finance_platform/service.py`
- `src/samchat/budgets/service.py`

Tests added:

- `tests/unit/gastos/test_finance_budget_tax_regressions.py`
- `tests/unit/gastos/test_finance_frontend_fixes.py`
- `tests/unit/gastos/test_payment_confirmation_dates.py`
- `tests/unit/test_ar_pending_income_links.py`
- `tests/browser/test_information_tables.py`
- `tests/browser/test_payment_proof_dates.py`

Tests updated: `tests/unit/gastos/test_payment_run_routes.py` and
`tests/unit/test_ar_read_model.py`. Documentation: this report and the table
inventory. No migration, application dependency or historical backfill.

## Original candidate commands and results

Environment: existing `/root/samchat/.venv/bin/python` (Python 3.12.3), with
`PYTHONPATH=src`; financial fixtures are synthetic and sessions are mocked.

- Final 29-file unit/adjacent suite: **293 passed, 2 failed**, eight existing
  deprecation warnings. Exact command/list and output are recorded in
  `/tmp/samchat-finance-fixes-unit-command.txt` and
  `/tmp/samchat-finance-fixes-unit-final.txt`; runnable coordinator command is
  `/root/samchat/.venv/bin/python /tmp/run_samchat_finance_fix_checks.py`.
- Browser command:
  `PYTHONPATH=src /root/samchat/.venv/bin/python -m pytest -q --no-cov tests/browser/test_information_tables.py tests/browser/test_payment_proof_dates.py`:
  **8 passed**. Synthetic actual theme/PaymentRun rendering, no app server or DB.
- Coverage command uses `coverage run --source=. -m pytest --no-cov` and
  `COVERAGE_FILE=/tmp/samchat-finance-fixes.coverage`; final XML is
  `/tmp/samchat-finance-fixes-coverage.xml`. Local comparison of executable
  changed lines against this XML: **118/125 = 94.4%**, above the 85% target.
  `/tmp/check_samchat_fix_diff.py` records the comparison and seven uncovered
  statements. This is a local calculation, not a remote CI gate receipt;
  embedded JavaScript is verified by browser behavior rather than Python
  coverage. `diff-cover` is unavailable.
- Python 3.12 AST parsing: **20 changed/new Python files passed**.
- `git diff --check`: **passed**.
- Affected-file lint ran with existing system flake8 under Python 3.12:
  `PYTHONPATH=src:/usr/lib/python3/dist-packages .../python -m flake8 <affected files> --ignore E501,W503,E203 --max-line-length 88`.
  It reports **428 existing diagnostics**, compared with **434** on exact HEAD
  sources; **no introduced diagnostics**, and none on changed executable lines.
  Whole-file lint therefore does not pass. Outputs are in
  `/tmp/samchat-finance-fixes-flake8.txt` and its `-baseline.txt` counterpart.
- Black, isort and mypy are not installed in the existing environment; those
  tool checks were not run and no tools/dependencies were installed.
- Canon SHA-256 values match the convergence register, before and after build.

## Known failures and attribution

Both final failures are in unchanged
`tests/unit/gastos/test_admin_budget_route_safety.py`:

- `test_assign_existing_rejects_negative_budget_amounts` expects the literal
  negative-budget guard in an existing route.
- `test_presupuestos_canonical_route_inventory_is_owned_by_budget_module`
  excludes two existing CFDI decision/collection routes from its expected set.

The exact HEAD version of `admin_budget_routes.py` was copied to
`/tmp/samchat-finance-fixes-baseline` and the same two tests failed there with
the same assertions. These are confirmed pre-existing failures; the candidate
changes neither that route module nor that test file. They were not repaired
outside the approved scope. The entire selected suite is consequently not green.

During construction, new fixture/expectation failures and the browser batch
JavaScript failure were corrected and their affected checks rerun. A plugin
coverage invocation caused duplicate SQLAlchemy registration during collection;
the final directory-based coverage run avoids that infrastructure behavior.
Browser checks run separately from async unit checks to avoid Playwright's
synchronous event-loop conflict.

## Review, deviations and remaining evidence

Independent acceptance verification maps all seven criteria to local tests,
with partial screen-by-screen coverage for reference placement. Read-only
implementation validation reports no remaining critical, important or minor
findings. It caught the late informe-link mapping gap and an accidental unrelated
colspan edit; both were corrected and re-reviewed before this report.

No deviation from the approved behavior. Additional files are necessary existing
read-model/UI owners discovered by the approved all-board inventory; strict audit
is an optional existing-service argument with unchanged defaults for other callers.

Residual evidence: no real PostgreSQL persistence/transaction integration,
authenticated full-app Finance UAT, real invoice/record reconciliation or
physical-touch-device acceptance. Not every reference board was individually
browser-tested. SPA file responses retain the middleware's existing exclusion.
No production state was changed or inspected. Deployment and historical repairs
require their own authorization/evidence. A future code rollback can restore the
base release; no database rollback is introduced by this candidate.

Canon unchanged: this restores existing catalog lineage, tax-component separation,
approval boundaries, payment evidence and information-board presentation. It uses
existing permissions, services and sources and makes no architecture, integration,
contractual-delivery or business-acceptance claim. Canon contents remain untouched.

## Approved delivery integration (2026-10-09)

Original approved commit: `96f4d82e39b525c2c5aa7ea1e5cf13ad8b36ba8f`.
Integrated current main: `7d857ad326a48e0c2a8315caf81f792f3279ce51`.
The user explicitly authorized commit, push, PR and merge; deployment was not
authorized. The primary dirty checkout remains outside the delivery worktree.

Integration adapts cutoff prefill to main's canonical `fecha_pago_efectiva` and
preserves scheduled `fecha_pago`, proof review/conflict reasons, detected-date
checks and sequential batch retry behavior. Cleanup keeps main's server filters,
focus and beneficiary column (12 columns after adding Operations). Pending CxC
links also pass main's strict tournament guard. These preserve existing scope,
authority and newer safeguards; there is no material spec deviation.

Current validation supersedes the original candidate counts above:

- Final 29-file suite using `/tmp/run_samchat_finance_fix_checks.py`:
  **422 passed**, 42 inherited warnings, no failures. The two old baseline
  budget-route failures are already corrected by current main.
- Additional merged payment/shared-invoice/browser suite: **124 passed and
  8 subtests passed**. Existing fixtures isolate the new helper/mapping seam;
  dedicated tests still exercise actual permission, mapping and audit behavior.
- Browser acceptance: **8 passed** (six information-table cases plus two payment
  date cases), in separate processes from async unit suites.
- AR adjacent verification: **87 passed**, plus two pending-link strict-scope
  negative regressions passed. Frontend/cleanup adjacent verification:
  **72 passed**. These overlap the final suite and are not additive counts.
- Current changed executable Python coverage: **114/121 = 94.21%** locally
  compared with `origin/main`. Remote changed-code coverage remains the merge
  gate authority; this local calculation is not a CI receipt.
- Full production/test `compileall`, diff hygiene, PR workflow contract,
  runtime packaging, registration-surface and accepted-regression checks passed.
- Route inventory regenerated after resolving the index: **576 routes,
  44 canonical actions, no parse
  gaps**; all nine inventory tests passed. Generated files are included in scope.
- Scoped lint reports **413 diagnostics both on current main and candidate**;
  none on changed lines. Whole-file lint is not green. Black/isort/mypy remain
  unavailable locally. An extra upstream ISH-upload suite requires missing
  `pypdf` and was not run locally; no application dependency was installed.
- Independent read-only validation found no unresolved code findings or
  material scope expansion. Canon hashes match the current convergence register.

Updated fixture files additionally include
`tests/unit/gastos/test_quick_shared_invoice_amounts.py`; regenerated delivery
artifacts additionally include `docs/private-plugin/route-inventory.json` and
`route-matrix.csv`. No other task scope is added.

Authenticated Finance UAT, real persistence/reconciliation and physical-device
acceptance remain pending. DIOT and production deployment remain excluded.

## CI correction (2026-10-10)

The initial PR run passed integration, policy and security but failed unit
inventory reproducibility, one budget projection expectation and one existing
browser fixture. The first generated inventory accidentally scanned duplicate
unmerged index entries; regeneration after commit fixes the artifact without
changing discovery rules. The budget expectation now verifies the Operations
reference returned by the approved projection. Browser fixtures adapt to the
new date helper while retaining payment/actor assertions. No gate, baseline,
application permission or production state is weakened to resolve these failures.

Correction verification: budget-service/inventory **49 passed**; critical browser
journeys/payment-date acceptance **25 passed**, including effective-date and
unchanged scheduled-date assertions with the real helper. Python compilation
and diff hygiene passed. CI is rerun on the corrected commit before merge.
