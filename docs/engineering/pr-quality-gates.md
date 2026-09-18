# Pull request quality gates

Every pull request into `master`, `main`, or `develop` must pass the aggregated
`Required PR gate`. Configure branch protection against that stable check name
instead of every matrix child.

## Mandatory checks

1. **Policy and repository contract** validates the CI definition itself,
   runtime/package consistency, registration boundaries, accepted regressions,
   Python compilation, and diff hygiene.
2. **Unit tests** run the complete `tests/unit` suite on Python 3.11 and 3.12.
3. **Integration tests** run `tests/integration` on Python 3.12 with Postgres 15
   and Redis 7.
4. **Changed-code coverage** requires at least 85% coverage on changed lines in
   pull requests. Python 3.12 unit and integration jobs publish coverage data;
   the coverage job combines it without rerunning the suites. This prevents new
   untested behavior without pretending that repository-wide historical
   coverage is already 85%.
5. **Filesystem security scan** blocks fixed high or critical findings detected
   by Trivy.

Pytest is invoked directly. A missing test runner, an empty required suite, a
failed test, a missing report, or a skipped mandatory job cannot produce a
green aggregate gate.

## Scope boundaries

Performance, load, destructive migration, production-data reconciliation, and
authenticated business UAT do not belong in the per-PR gate. They require the
Nightly/RC or release workflow and remain separate from repository correctness.

## Adding or changing behavior

- Add positive, negative, authorization, and boundary tests for the changed
  behavior.
- Put isolated tests in `tests/unit` and service-backed tests in
  `tests/integration`.
- Never add `continue-on-error`, `|| true`, or a file-existence condition to a
  mandatory check.
- Update `scripts/ci/check-pr-quality-gate.py` when the mandatory architecture
  changes.

## Branch protection

After this workflow lands, reconcile the protected-branch required checks and
require exactly `Test Suite / Required PR gate` (as displayed by GitHub). Remove
stale required-check names only after the new check has completed successfully
on the default branch.

Canon unchanged: this gate changes repository verification only. It introduces
no product capability, authority, data model, workflow state, or deployment
claim.
