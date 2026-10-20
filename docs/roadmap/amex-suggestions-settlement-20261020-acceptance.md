# AMEX suggestions and settlement acceptance

Date:2026-10-20. Approved scope: `amex-suggestions-settlement-20261020-story.md` and corresponding spec. Status: independent accountant-browser acceptance4PASS against the FINAL frozen runtime and migration6cce7e4f, including actual uploaded historical XML. The original-UUID export defect is resolved; READ ONLY preview and all-format fiscal evidence remain verified. This report does not authorize publication, production migration, actual financial operations or declare Finance UAT complete.

## Acceptance coverage

| Acceptance criterion | Coverage method | Result | Notes or gaps |
| --- | --- | --- | --- |
| AC1 automatic candidates, manual search | Actual browser R1 starts with CSV upload and initial statement; supporting SQL whole-catalog ranking and route manual-search tests | PASS for actual R1 | Initial statement discovers Suntory without UUID/q input, no SAT request or automatic match. Manual-search and ranking-negative results belong to focused owner suites. |
| AC2 independent dates/cross-month | Actual R1 Sep29 charge / Oct6 invoice /3814; supporting SQL distant-year/no-date tests | PASS | Explicit Sep30 accounting date/open period retained; no automatic move to invoice month. |
| AC3 evidence/confirmation/no inferred propina | Actual candidate UI includes bank/fiscal dates, amount, PPD and review; explicit status observation and reason/confirmation; supporting ambiguity/amount-delta/identity tests | PASS for representative browser case | Real UUID identity and all exported movements checked. Ambiguous alternatives and invalid identity use supporting focused tests. |
| AC4 distinguish PUE/PPD and card payment | Independent fiscal-reader matrix plus actual PPD browser R1 and physical payment owner with/without cut | PASS | Earlier independent69 fiscal/request-unit tests passed; coordinator final909-unit receipt covers the final runtime. Real payment posts only liability/bank. |
| AC5 all Plataforma cards, optional REP, real XML IVA | Independent5cards×PUE/PPD×REP present/absent/invalid reader matrix; actual browser configured Platform RFC, PPD/no REP, physical IVA526.07 | PASS | Snapshot freezes evidence version, PPD, not_local, policy_applies and rep_requiredFalse. Real invoice matching remains business UAT. |
| AC6 Finance classification→CP line only | Actual browser Finance actor classifies project/category; separate CP actor assigns existing line; actual budget owner and journal | PASS | One budget assignment; CP has no tournament selector; original flow and mappings reused. |
| AC7 original expense-account credit | Actual user_upload original XML ingestion→expense/document→comprobacion owner posting→CSV import; browser selects physical original credit and posts settlement | PASS after export correction | Uploaded provenance is asserted on the actual canonical CFDI; no SAT request is made for R3. Exactly2physical movements: historical debtor debit/AMEX credit; no extra IVA/budget; auxiliary saldo0 and comprobado116. Original CFDI UUID is exported exactly once in each format. |
| AC8 immutable evidence/replay | Browser freeze removes confirm/source controls; identical confirmation returns same cut; settlement count1; actual READ ONLY preview before confirm | PASS for actual journeys | Supporting settlement PG suite covers late rollback, frozen originals, concurrent same/distinct imports and source-mutation races; its complete execution receipt is recorded by the builder/coordinator. |
| AC9 existing card payment | Existing PostgreSQL `test_payment_owner_schedules_and_posts_only_liability` parametrized with/without cut | PASS:2cases | Scheduling creates no journal; payment physically debits AMEX and credits bank, no added expense. No production payment performed. |

R2-only and mixed R1/R2/R3 are exercised by the settlement builder's actual-owner integration tests, not invented in this browser fixture. The browser acceptance verdict does not replace the independent module-wide SQL negative verification or final implementation validation.

## Actual browser boundary and strong checks

New tests: `tests/acceptance/test_amex_suggestions_settlement_journey.py`. Existing live-owner server/direct-PG fixtures and settlement historical-credit producer are imported without copying financial fixture semantics. Only authenticated local actor/session selection and external SAT transport are substituted. Accounting, fiscal parsing/ingestion, status receipts, budget, source-credit validation, SQL guards, posting, auxiliary and exporters remain real.

Disposable PostgreSQL is hard restricted by the existing fixture to localhost55471/test_user/devnous_test. Owner migrations and deferred triggers remain enabled. Chromium uses cached binaries; no dependencies were installed.

Before confirmation each actual complete proposal runs in a new PostgreSQL `TRANSACTION READ ONLY`. A listener observes actual statements and rejects DML/DDL, advisory locks and FOR UPDATE/SHARE; both proposals remain ready. Preview movement totals are balanced and visible in the browser. All3download formats are parsed, every movement's exact account/debit/credit multiset matches the frozen cut, and the original CFDI UUID occurs exactly once. ZIP-contained supported files receive the same checks. XLSX RFC cells are verified untrimmed against their canonical values, without masking whitespace defects.

## Commands and results

Environment prefix for PG/browser:

```sh
PYTHONPATH=src:. TEST_AMEX_DATABASE_URL=postgresql+asyncpg://test_user@127.0.0.1:55471/devnous_test COVERAGE_FILE=/tmp/amex-suggestions-acceptance-final-20261020.coverage /root/samchat/.venv/bin/python -m coverage run --rcfile=/tmp/amex-suggestions-units-20261020.coveragerc -m pytest --no-cov tests/acceptance/test_amex_suggestions_settlement_journey.py tests/acceptance/test_amex_sat_budget_direct_postgres.py::test_payment_owner_schedules_and_posts_only_liability -q --tb=short
```

Final result: **4passed,0skipped,151inherited deprecationwarnings,29.00s**. Log: `/tmp/amex-suggestions-browser-acceptance-20261020-final-covered.log`. Coverage artifact: `/tmp/amex-suggestions-acceptance-final-20261020.coverage` (118784bytes). Coordinator separately reported99PGpassed/1explicitreceipt-regenerator skip and909unitpassed on this final frozen runtime; those are supporting owner results, not a duplicate independent execution here.

Earlier independent fiscal/request regression (prior to final historical-origin hardening; final full unit receipt belongs to coordinator):

```sh
PYTHONPATH=src:. /root/samchat/.venv/bin/python -m pytest tests/unit/gastos/test_amex_direct_fiscal_evidence.py tests/unit/gastos/test_amex_card_payment_service.py -q
```

Result: **69passed,2deprecationwarnings**. Log: `/tmp/amex-suggestions-acceptance-fiscal-payment.log`.

Existing release venv black --check, isort --check-only and full focused flake8 --max-line-length=88 pass for the new acceptance test. Test collection:2tests. These scoped style checks do not establish whole-repository lint/type completeness.

## Counterexample and resolution

Initial4case run had3passes and a test-only numeric comparison failure after R1 completed exports: legacy SUM(debe) returns float, so exact assertion now converts through Decimal(str(value)). No runtime change was needed for this assertion correction.

Stronger UUID export assertions then exposed a real R3 defect: movements were correct, but the original CFDI UUID was absent from XLSX. The implementation owner fixed historical-source fiscal evidence freezing; the assertions were preserved. Final4case run verifies the fix through actual routes and all3formats. Earlier logs remain `/tmp/amex-suggestions-browser-acceptance-20261020.log` and `...-rerun.log`; they are not the final verdict.

## Stability and limits

Before/after SHA256 checks passed for all10critical runtime, fixture and SQL sources:

| Source | SHA256 |
| --- | --- |
| `src/devnous/gastos/services/amex_direct_accounting_service.py` | `fa50afa0979456579740342e9cf5fdcb8159b744905ff48fb42e361a5a5f8f2f` |
| `src/devnous/gastos/services/amex_expense_account_settlement_service.py` | `ee3c4672efa74e669cd0338ff2166aeb0139310991b033e90207da2b333ff841` |
| `src/devnous/gastos/services/amex_direct_matching_service.py` | `49ca3a0f93938e9df1f99f6279724c2c1030a946fe3d592b68650849f2ffec7a` |
| `src/devnous/gastos/services/amex_direct_fiscal_evidence_service.py` | `06065554bcbdff6d1d7a62b4af2fbcca78e21a5976e2348d12b9beab9d8fa8f8` |
| `src/devnous/gastos/services/amex_direct_schema_contract.py` | `65c85200d121453d2ab2e85c761fc882b73a3b43a5a44b7234ee4e404c25e87f` |
| `src/devnous/gastos/routes/admin_amex_direct_routes.py` | `8266d2a93ee72644e115d53c1d406e38bc87b7d9a0161c1434cc85e15fb7fac9` |
| `database/migrations/20261020_amex_expense_account_settlements.sql` | `6cce7e4f62336837e78b2fa8a75b0ba716a114c6c9ebc43c8237b4cdad12816f` |
| `tests/acceptance/test_amex_qa_browser_accounting_journey.py` | `8b4a8ed927a69dcda6c7907e970018d78a6dc6cb220a9ef2574211214a9e001b` |
| `tests/acceptance/test_amex_sat_budget_direct_postgres.py` | `691fb4d0c51e9b7d5342e7abb7c7c7bc3bf000988952adc5fa9cd4d0821c99b0` |
| `tests/integration/test_amex_settlement_postgres.py` | `8cceef37a7fe63ed6ecbb41a99b42a44346cdc76732eed9fa7abc853c5466b40` |

Final receipt: `/tmp/amex-acceptance-final-runtime-20261020.sha256`. Earlier passing-baseline receipts/logs are preserved as historical evidence and do not substitute for this final run. PostgreSQL lease was released after final execution; no test process remains active. Synthetic fiscal evidence proves software behavior, not actual Suntory correspondence or financial acceptance. No production/SAT network call, commit, migration or deployment performed. No spec deviation or unresolved acceptance defect found in these executed journeys.

Canon bootstrap verified in required order: product65f8116a…, engineering8eebb3c5…, sweep2c55cc80… match refreshed register. Canon unchanged by this test-only evidence; approved implementation canon is maintained by its designated owner.
