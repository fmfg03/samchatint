# AMEX selected-SAT concurrency review correction — 2026-10-16

PR #491 publication follow-up, starting at `7ea82f2e1`. The user authorized commit, push, PR and merge. GitHub CI on that head passed: 5595 repository unit tests; 464 integration tests with five inherited skips; 91 browser tests; changed-line coverage 94% over 642 lines; Required PR gate PASS. Those results are historical evidence for that head, not the final concurrency correction.

## Finding and approved contract

The unresolved P2 review at https://github.com/fmfg03/samchatint/pull/491#discussion_r4296055223 correctly identified that selected-CFDI consultation checked request-key presence without reserving the external request. Concurrent submissions could both contact SAT and append observations. The approved specification’s selected-status contract and acceptance plan already require bounded SAT consultations and concurrent-use coverage. This correction completes that contract without changing financial authority, business classification, schema, dependencies or external-call budgets.

## Implementation

The selected consultation persists a bounded lease in existing statement `progress_json`, carrying request key, unique attempt token, selected item/CFDI and source/fiscal identity hashes. It acquires that lease under the canonical owner lock before closing the transaction for SOAP. Selected and batch consultations reject an active counterpart lease. Completion revalidates authority, current source/fiscal identity and attempt ownership. Cleanup follows import/state lock order and releases only its own token; it preserves a propagating original error and permits lease expiry after interruption. The existing exact fresh status receipt supplies replay reuse. Batch cursor data remains intact. Selected requests on the same statement are serialized, including different client keys.

Changed files: `src/devnous/gastos/services/amex_direct_status_service.py`, `tests/acceptance/test_amex_direct_selected_cfdi_authority.py`, `tests/unit/gastos/test_amex_direct_cfdi_search.py`, this report and its validation/evidence follow-up. The integration bridge automatically discovers seven added parameterized cases.

## Verification

Builder execution: 15 PostgreSQL cases (12 selected-authority/concurrency plus three real HTTP cases) PASS in27.85s; 25 focused unit tests PASS in2.21s. Black, isort, strict Flake8 and focused source mypy PASS. Event-gated PostgreSQL tests establish one external call/receipt under simultaneous same/different request keys, batch exclusion, cached replay, retry after error/timeout/cancellation, superseded-token protection and expired-lease recovery preserving batch cursor. External SAT responses remain synthetic.

Final execution: **816 tests PASS**, 1453 inherited warnings, 149.98s, plus **30 budget unit tests PASS** in4.54s, totaling **846 PASS** (797 units +46 bridged acceptance +3 HTTP). Fresh thread/greenlet coverage: **630/679 changed executable Python statements,92.78%**. Independent validation: **CLEAN_WITH_NOTES**, no findings; see `amex-qa-remediation-20261016-selected-sat-validation.md`. Commands and source fingerprints are recorded in `docs/evidence/amex-qa-remediation-20261015/selected-sat-concurrency-20261016.json`. Fresh GitHub checks are required on the final published head before merge. No live SAT call, production data repair, migration or deployment has been performed.

## Canon and limits

Canon unchanged: this enforces the already-approved bounded-observation, closed-transaction and separate-linking contracts. No financial semantics or architecture change requires a new appendix. Original reports/fingerprints remain historical evidence; the status service, selected-authority acceptance tests and selected unit fixture now intentionally differ from the earlier manifest because of this reviewed correction. No claim of authenticated Finance UAT is made.
