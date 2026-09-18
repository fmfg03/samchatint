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
5. **Python security baseline** blocks new dependency advisories and new
   high-severity, high-confidence Bandit findings.

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

## Accepted security baseline

The PR gate names current debt instead of hiding it or making every unrelated
PR permanently red:

- ChromaDB 1.5.9 currently resolves from the open dependency constraint and has
  unresolved advisories `PYSEC-2026-311`, `PYSEC-2026-3813`,
  `PYSEC-2026-3814`, and `PYSEC-2026-3815`. `pip-audit` reported no fix version
  on 2026-09-18. Only these IDs are ignored; a new advisory fails the gate.
- Bandit rule B324 has six existing findings: three SHA-1 uses in the SAT/XML
  signature implementation and three MD5 uses for cache keys or deterministic
  feature bucketing. B324 is temporarily excluded until those protocol and
  non-security hashing uses are adjudicated separately. All other new
  high-severity, high-confidence Bandit findings fail the gate.

These exceptions are not claims of safety. Remove each exception when the
underlying dependency or code path is remediated.
