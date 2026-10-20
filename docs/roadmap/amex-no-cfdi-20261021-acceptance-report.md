# AMEX Sin CFDI — independent acceptance

2026-10-21. Candidate worktree `amex-no-cfdi-20261021`; baseline `fac1d1db361f7dc27cd94409f801afef3e9881c7`. Approved story and specification are the versioned `amex-no-cfdi-20261021-{story,spec}.md`. No production activity, Git publication, or deployment forms part of this assessment.

Independent verifier scope: acceptance behavior and tests only. Product implementation remains unchanged by this verifier. Canon bootstrap completed in required order; all three SHA-256 values match the convergence register (product `0596b7e5…`, engineering `c44096c6…`, sweep `2c55cc80…`).

| Acceptance criterion | Coverage method | Result | Notes / gaps |
| --- | --- | --- | --- |
| Explicit declaration, reason, confirmation, audit; absence alone is insufficient | Service and route tests; SQL receipt seal attacks; browser bulk declaration | PASS — builder execution receipt; test assertions inspected | No implicit conversion from missing fiscal evidence. |
| Preserve original banking source; no invented invoice/RFC/UUID/report/taxes | Signed-profile service/PG journeys; browser verifies no documentos; exact two-line no-tax journal | PASS — builder execution receipt; test assertions inspected | Merchant-only bank RFC is preserved as bank evidence, never exported as a fabricated invoice. |
| Existing accounting/CP authority and isolation | Independent `test_no_cfdi_authority_is_reloaded_before_any_effect`, six denial variants | PASS — independent real PostgreSQL run | Inactive, CP-only, unscoped, card-context removed, entity-context removed, explicit conciliar denial. |
| Bulk atomicity, stale versions, payload conflict, replay | Backend revision-chain and invalid-batch tests; independent same-request race | PASS — independent real PostgreSQL run | SQL guards remain enabled. |
| Classify → send → CP assignment → preview → full-state confirmation | Backend signed-profile journeys plus real browser journey | PASS — builder execution receipt; test assertions inspected | Full bank amount used; pending budget replaced by one ledger actual. |
| Revoke before treatment/source changes; immutable historical decisions | Backend context lifecycle; independent revoke → recovery → redeclare journey | PASS — independent real PostgreSQL run | Recovered effective source requires a new economic fingerprint; old head remains historical. |
| No simultaneous fiscal/no-fiscal fork | Independent actual declaration/declaration, auto-match, context and recovery races; revoke/confirmation race | PASS — independent real PostgreSQL run | A loser may need refreshed versions; never silently replaces the winning treatment. |
| Whole mixed state, one new policy, preserve prior recognition | Independent five-kind cut: new CFDI, new_no_cfdi, report-prior, debtor-settlement, credit exclusion | PASS — independent real PostgreSQL run | Uses real historical owners, not synthetic coverage dictionaries. |
| Preview read-only | Browser journey executes preview inside PostgreSQL READ ONLY | PASS — builder execution receipt; test assertions inspected | No financial owner mocks; SAT transport is not configured. |
| Frozen decision and exact journal integrity | SQL attacks against receipt, head, missing seal, reason, treatment, amount, account and tax movement | PASS — builder execution receipt; test assertions inspected | Late failures must roll back complete cut even when caller catches exception. |
| Frozen CSV/XLSX/ZIP, safe reason text, no fake fiscal fields | Backend export and real browser download checks | PASS — builder execution receipt; test assertions inspected | Formula-like reason cannot become an XLSX formula. |
| Old hashes and existing CFDI/R2/R3 behavior | Legacy absent-pointer hash unit; existing fiscal/prior/settlement regression suites | PASS — builder execution receipt; test assertions inspected | General regressions do not substitute for the new five-kind-cut case. |
| Schema owner migration only, guarded rollback | Real PostgreSQL schema inspection and empty rollback round-trip/history refusal | PASS — builder execution receipt; test assertions inspected | No production migration was run. |

## Independent commands and results

- Black88, isort and flake8 (`--max-line-length=88`) for the new independent test file: passed.
- Independent PostgreSQL acceptance: **13 passed**, 383 inherited SQLAlchemy/datetime deprecation warnings, **24.34 seconds**, exit0. Exclusive disposable database lease at `127.0.0.1:55471`, role `test_user`, database `devnous_test`; fixture creates/drops isolated schemas with all owner migrations and guards enabled. Lease released after completion.

```sh
PYTHONPATH=src:.:tests/acceptance:tests/unit/gastos:/tmp/samchat-amex-source-testdeps \
TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test \
/root/samchat/.venv/bin/python -m pytest -q --no-cov \
  tests/acceptance/test_amex_no_cfdi_acceptance.py
```

Raw final output: `/tmp/amex-no-cfdi-independent-acceptance-20261021.log`.

Initial ten-case run had eight passes and two harness expectation failures: removed card/entity configuration raises safe `DirectConflict`, rather than `PermissionError`. Updated only these test expectations; the final thirteen-case run passes. No product change was necessary. The initial raw log was overwritten on rerun; `/tmp/amex-no-cfdi-independent-initial-failure-20261021.txt` explicitly preserves a reconstructed failure summary and the original traceback remains in tool output.

Builder receipts relayed by the coordinating owner (not rerun by this verifier): 1061 AMEX unit regressions passed; backend 15 PostgreSQL and24 unit cases passed against final DDL; schema 58 focused cases passed; actual browser full round trip passed in9.71 seconds. This verifier inspected their acceptance assertions and independently ran only the thirteen additional gap-focused cases.

## Explicit remaining limits

All requested conflict categories have bounded real-transaction tests. Each race ran once with simultaneous task start; this is not exhaustive scheduler/interleaving or load evidence. At cut-confirmation time declaration/revocation is already forbidden by budget submission/assignment; the actual race tests revocation against confirmation and requires one immutable declaration and one consumption/policy. It does not invent an otherwise eligible declaration at that lifecycle stage.

The five-kind cut combines new fiscal and no-fiscal recognition, actual prior report recognition, actual debtor-account settlement and an excluded bank credit. It produces exactly one additional policy with464 debit/credit, only two new budget assignments, a full232 no-fiscal expense and no fiscal identity for that charge. The recovery lifecycle preserves the old declaration and revocation and binds redeclaration to the new economic fingerprint.

The new report-specific tests do not independently repeat every old frozen-export/catalog-mutation case; existing regression receipts support those invariants. Technical synthetic journeys are separate from authenticated Finance UAT on real charges and are not fiscal/business acceptance. No production migration, publication or deployment occurred.


## Changed files and deviations

Independent verifier changed only `tests/acceptance/test_amex_no_cfdi_acceptance.py` (new acceptance cases) and this report. Black88/isort/flake8 passed for the new test file. No implementation deviations or open acceptance failures were found in this bounded verification. The required read-only implementation validator remains a separate next checkpoint.

## Verificación después de I1

El validador encontró I1 en la igualdad de multiplicidades de los snapshots; no invalida los casos de negocio anteriores, pero impedía aprobar la integridad del cambio. Tras la corrección completa del guard y congelación del DDL, el coordinador repitió esta suite independiente y el recorrido real de navegador conjuntamente: **14 passed**, 419 advertencias heredadas, **31.77s**, exit0, `/tmp/amex-no-cfdi-final-affected-acceptance-20261021.log`. Las aserciones y los owners de estas pruebas no cambiaron. SQL final SHA256 `4fa8d42850e05db21c4b370771114f063f79c38f808564a44b5c6ab1c3e4ca7e`. I1 fue cerrado después por el validador mediante reproducción y ocho variantes de rechazo independientes; véase `amex-no-cfdi-20261021-validation-report.md`.
