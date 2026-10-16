# AMEX QA remediation — build report, 2026-10-15

Base: `a983be79e1ad8a6b5aa56affe27a945160ac8166`. Branch: `qa/amex-accounting-journey-20261015`. Approved story, technical specification and exact canon amendments remain the scope. Implementation is isolated; primary checkout and production untouched.

La corrección ya recorre el CSV original completo en aislamiento: conserva sus evidencias, recupera sólo cinco cargos y genera una póliza de 145 cargos, con descargas verificadas. Los CFDI y las decisiones presupuestales del caso positivo son sintéticos; producción no fue migrada, recuperada ni desplegada.

## Result and purpose

- Reject inconsistent new statement periods before persistence; preview and intake share one evaluator.
- Owner-run append-only recovery preserves the original file, header and physical rows, reconstructing only proven omitted debit sources. Canonical SQL view and explicit Python resolver feed all fiscal/budget/accounting consumers and guards.
- Business-data CFDI finder replaces internal-PK entry; selected SAT observation and manual linking retain distinct authority, scope, consent, version and global-usage controls.
- Project/torneo controls dependent categories, including Dirección General; Save and Send have separate confirmations. CP assigns budget and cannot act on recognized entries.
- Review exposes all blockers/responsible actions and canonical Debe/Haber proposal. Frozen multicargo exports compare exact economic multisets and preserve duplicate multiplicity.
- Added real-owner browser journey with separate Finance/CP actors and exact downloaded-file assertions; full original CSV is a separate scenario with explicitly synthetic fiscal/budget evidence.
- Corrected real integration findings: missing physical row_number on finder, expired ORM actor after rollback, and global/folio lock ordering under concurrent intake/recovery.

## Validation receipts

- AMEX unit aggregate: **797 passed**,38 warnings,23.06s; `/tmp/amex-remediation-unit.log`.
- Route inventory reproducibility: **9 passed**,23.36s; `/tmp/amex-remediation-inventory-tests.log`. Inventory regenerated after final route changes and staging its three new source modules for tracked-source discovery; no commit created.
- Recovery actual-owner browser: **1 passed**,39 warnings,13.50s; `/tmp/amex-remediation-recovery-browser.log`. CSRF, no consent and stale version reject before writes; exact retry creates no duplicates. Initial test expected a redirect for stale state; corrected assertion to actual HTTP409 conflict, without product change.
- Strengthened actual-owner browser: **1 passed**,47 warnings,19.32s; `/tmp/amex-remediation-browser-real-owner.log`; durable receipt in `docs/evidence/amex-qa-remediation-20261015/`.
- Recovery owner independent build: **30 PostgreSQL +20 schema-admission passed**,92.59s; `/tmp/amex-recovery-final-tests.log`. Recovery92% / resolver97% coverage.
- Final PostgreSQL aggregate: **404 passed**,8496 inherited warnings,1032.39s,18 files in one serial invocation; `/tmp/amex-remediation-pg.log`. No required PG case skipped or failed.
- Full original148-row recovered statement and exact3format downloads: **1 passed**,1620 inherited warnings,325.62s; `/tmp/amex-remediation-full-statement.log` (--no-cov, coverage separately established). Originalheader/148sources/140ExpenseIDs preserved;5newExpenses;145debits/3explicitcredits;145syntheticCFDIs,145budgetassignments by distinct CP; one balanced policy; exactXLSX/CSV/ZIP economic and fiscal content; replay samecut; directSQLchange to recoveredExpense aftercut rejected. Durable receipt: `docs/evidence/amex-qa-remediation-20261015/full-statement-result.json`.
- Changed executable source coverage: **549/642 statements,85.51%**. Passing unit, browser, recovery-browser, final PG and owner recovery coverage only; interrupted/failed full coverage excluded. `coverage.analysis2` executable statements intersected with added lines from `git diff HEAD --unified=0` (untracked new modules included fully). Per-file/missing-line evidence: `docs/evidence/amex-qa-remediation-20261015/changed-source-coverage.json`. This is changed-code coverage, not whole-repository coverage.
- Independent acceptance: **PASS AC1–AC8**, `amex-qa-remediation-20261015-acceptance-report.md`; fresh log/receipt evidence reviewed by a separate verifier.
- Read-only final implementation validation: **CLEAN_WITH_NOTES**, no Critical/Important/Minor findings; `amex-qa-remediation-20261015-validation-report.md`. Independent60 scoped tests pass; source40/40 and original-evidence10/10 hashes match. No correction cycle required.
- Mypy: **13 scoped source files pass** across `/tmp/amex-remediation-mypy.log` and `/tmp/amex-remediation-ui-mypy.log`.
- Black:36 scoped implementation/test files pass across aggregate34 and subsequent2 additions; flake8 excluding inherited E501 passes initial34; subsequent2 pass strict max88 lint. Zero added source/test lines exceed88.
- Import sorting: **34 owned files pass**; new recovery browser and schema test formatting/lint pass. Broad isort reports pre-existing imports in access_control_service.py and shared original direct fixture; changed lines there are only one action and one migration entry.
- Broad formatting/lint on models.py reports identical pre-existing debt (Black, three F401 and E501); preserved bounded re-export. Original assessment forensic reproducer remains byte-for-byte as recorded and is excluded from implementation lint.

## Commands

Test interpreter `/root/samchat/.venv/bin/python`; `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps`; isolated `TEST_AMEX_DATABASE_URL` on localhost55471 only. `pytest -q -o addopts='' -o asyncio_mode=auto --cov=devnous.gastos --cov-report= --cov-fail-under=0` with the unit, browser, full-statement and PostgreSQL paths. Full original CSV is opt-in via `AMEX_QA_ACTIVITY_CSV=/root/samchat/activity.csv`, pinned SHA in test; its bytes are private and uncommitted.

Existing tools interpreter `/srv/samchat/venvs/release-598e7a286/bin/python` runs scoped Black/isort/flake8/mypy, no dependency installed. Route inventory: `PYTHONPATH=src:. .../python scripts/private_plugin/inventory.py`. Canon SHA-256 matches convergence register; product and engineering contain only human-approved appendices, sweep unchanged. `git diff --check` passes.

## Failures and attribution

- Product failures found through actual-browser/concurrent acceptance were corrected within approved scope and have regressions, as described above.
- Earlier root harness issues (duplicate keyword, wrong FIN_PARTIDAS column/CSV encoding, teardown masking, mixed loaded helper version) corrected; final fresh serial evidence supersedes them. Full intermediary run intentionally interrupted when helper assertions changed, rather than accepting mixed-loaded results.
- Concurrent isolated schemas still share AMEX global advisory locks: earlier cross-job SAT try-lock failure is test-infrastructure interference; final PostgreSQL runs are serial. No failed required final check is treated as passed.
- Infrastructure interruption: Docker restarted at20:16:44CEST (verified service start). Disposable --rm database disappeared; original full run failed344.76s with connection loss/teardown error. Root recreated only its test PostgreSQL without --rm and repeats affected acceptance. Durable metadata: `docs/evidence/amex-qa-remediation-20261015/infrastructure-interruption.json`. This is not a passing full test.
- Original QA red results remain under `docs/evidence/amex-accounting-qa-20261015/`; remediation evidence is separate. SQLAlchemy/utcnow warnings are inherited and unmodified.

## Scope and operational limits

No deviations to approved business behavior. No production dependency, DDL at startup, backfill, automatic period widening, fabricated report, live SAT call, production posting or private CSV commit. Migration SQL is prepared and tested, **not applied in production**. Recovery is separately explicit per eligible state; deploying code alone does not change its140/5/3 original evidence. Migration, release and actual data recovery remain separate approvals. Activate the owner DDL and compatible code in a coordinated release: guard hashes are deliberately versioned and incompatible admission fails closed. No startup DDL or automatic recovery is available. Empty-only rollback is tested; populated recovery tables deliberately prohibit destructive downgrade.

Browser test uses synthetic16%IVA XML; full CSV uses synthetic zero-tax XML and invented aliases, never real fiscal assertions. Passing these is technical acceptance, not authenticated Finance UAT or proof that actual SAT invoices correspond. Payment screen/owner remains existing and tested; no payment executed. Fourteen historical review exceptions remain outside scope.

## Changed files

- `SAMCHAT_CANON_FOR_CHATGPT_2026-09-10.md`
- `SAMCHAT_ENGINEERING_CANON_FOR_CHATGPT_2026-09-10.md`
- `database/migrations/20261015_amex_direct_period_recovery.sql`
- `database/migrations/20261015_amex_direct_period_recovery_rollback.sql`
- `docs/evidence/amex-accounting-qa-20261015/acceptance-verdict.json`
- `docs/evidence/amex-accounting-qa-20261015/assessment-validation.md`
- `docs/evidence/amex-accounting-qa-20261015/browser-journey.json`
- `docs/evidence/amex-accounting-qa-20261015/catalogue-readiness.json`
- `docs/evidence/amex-accounting-qa-20261015/full-csv-accounting-receipt.json`
- `docs/evidence/amex-accounting-qa-20261015/independent-journey-review.md`
- `docs/evidence/amex-accounting-qa-20261015/isolated-assessment.json`
- `docs/evidence/amex-accounting-qa-20261015/isolated-assessment.md`
- `docs/evidence/amex-accounting-qa-20261015/production-audit.json`
- `docs/evidence/amex-accounting-qa-20261015/reproduce_multicharge_export.py`
- `docs/evidence/amex-accounting-qa-20261015/sha256-manifest.json`
- `docs/evidence/amex-qa-remediation-20261015/browser-real-owner-result.json`
- `docs/evidence/amex-qa-remediation-20261015/changed-source-coverage.json`
- `docs/evidence/amex-qa-remediation-20261015/full-statement-result.json`
- `docs/evidence/amex-qa-remediation-20261015/infrastructure-interruption.json`
- `docs/evidence/amex-qa-remediation-20261015/source-sha256.json`
- `docs/evidence/amex-qa-remediation-20261015/verification-summary.json`
- `docs/private-plugin/route-inventory.json`
- `docs/private-plugin/route-matrix.csv`
- `docs/roadmap/amex-accounting-journey-qa-20261015.md`
- `docs/roadmap/amex-qa-remediation-20261015-acceptance-report.md`
- `docs/roadmap/amex-qa-remediation-20261015-build-report.md`
- `docs/roadmap/amex-qa-remediation-20261015-spec-review.json`
- `docs/roadmap/amex-qa-remediation-20261015-spec.md`
- `docs/roadmap/amex-qa-remediation-20261015-story.md`
- `docs/roadmap/amex-qa-remediation-20261015-validation-report.md`
- `docs/roadmap/samchat-convergence-register.md`
- `src/devnous/gastos/amex_direct_models.py`
- `src/devnous/gastos/amex_schema_policy.py`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_amex_direct_routes.py`
- `src/devnous/gastos/routes/user_routes.py`
- `src/devnous/gastos/services/access_control_service.py`
- `src/devnous/gastos/services/amex_accounting_identity_service.py`
- `src/devnous/gastos/services/amex_cut_export_service.py`
- `src/devnous/gastos/services/amex_direct_accounting_service.py`
- `src/devnous/gastos/services/amex_direct_budget_service.py`
- `src/devnous/gastos/services/amex_direct_cfdi_search_service.py`
- `src/devnous/gastos/services/amex_direct_common.py`
- `src/devnous/gastos/services/amex_direct_context_service.py`
- `src/devnous/gastos/services/amex_direct_effective_source_service.py`
- `src/devnous/gastos/services/amex_direct_intake_service.py`
- `src/devnous/gastos/services/amex_direct_matching_service.py`
- `src/devnous/gastos/services/amex_direct_recovery_service.py`
- `src/devnous/gastos/services/amex_direct_schema_contract.py`
- `src/devnous/gastos/services/amex_direct_schema_service.py`
- `src/devnous/gastos/services/amex_direct_status_service.py`
- `src/devnous/gastos/services/amex_recognition_service.py`
- `tests/acceptance/test_amex_direct_period_recovery_postgres.py`
- `tests/acceptance/test_amex_direct_selected_cfdi_authority.py`
- `tests/acceptance/test_amex_effective_consumers_postgres.py`
- `tests/acceptance/test_amex_qa_browser_accounting_journey.py`
- `tests/acceptance/test_amex_qa_full_statement_journey.py`
- `tests/acceptance/test_amex_qa_period_recovery_browser.py`
- `tests/acceptance/test_amex_sat_budget_direct_postgres.py`
- `tests/integration/test_amex_direct_backend_postgres.py`
- `tests/integration/test_amex_direct_schema_postgres.py`
- `tests/unit/gastos/test_amex_approved_informe_routes.py`
- `tests/unit/gastos/test_amex_card_identity_routes.py`
- `tests/unit/gastos/test_amex_cut_exports.py`
- `tests/unit/gastos/test_amex_direct_cfdi_search.py`
- `tests/unit/gastos/test_amex_direct_routes.py`
- `tests/unit/gastos/test_amex_direct_schema_admission.py`
- `tests/unit/gastos/test_amex_direct_source_service.py`
- `tests/unit/gastos/test_amex_effective_identity.py`

## Operator path verified by browser

Open `/admin/contabilidad/amex/estados`, upload the original and declare a period containing all parsed dates. Continue to the same direct state. If an eligible older DIRECT state omitted period rows, use the explicit period preview/confirmation before ordinary processing; original evidence remains visible. For each unresolved charge, use Buscar CFDI, inspect business data, consult vigencia if needed and explicitly link the selected invoice. Choose Proyecto / torneo and its catalog category, Guardar clasificación, then separately confirm Enviar a Control Presupuestal. The distinct CP actor assigns the budget line; Finance returns to review all blockers and Debe/Haber proposal, confirms one cut and downloads XLSX/CSV/ZIP. Existing payment owner remains the route for card payment; no new payment is generated by this remediation.

## Cierre para revisión humana

Implementación, aceptación y validación independiente terminadas. Candidato de 69 archivos preparado en el índice del worktree aislado; diff completo revisable en `/tmp/samchat-amex-qa-remediation-20261015.patch`. No hay commit, push, PR, merge ni despliegue de esta corrección. Se eliminó únicamente la base temporal propia después de verificar cero conexiones pendientes; se conservaron los recibos, hashes y archivos de prueba.

## Actualización de publicación y medición — 2026-10-16

Tras la aprobación final y autorización de publicación, el candidato se publicó como commit `3170b73a2` en PR #491. El cierre anterior describe el punto de control previo a esa autorización. La primera ejecución CI reveló descubrimiento incompleto de aceptación e instrumentación sin greenlet. El seguimiento acotado incorpora 39 casos originales a integración, tres casos HTTP y configuración explícita de concurrencia; no cambia las 40 huellas originales de código/pruebas. La medición histórica de85.51% queda sustituida por **594/642 (92.52%)**, obtenida exclusivamente de datos frescos correctamente instrumentados: **839 pruebas aprobadas**, más **5 de navegador**. Validación independiente del seguimiento: **CLEAN_WITH_NOTES**, sin hallazgos. Véanse los reportes `amex-qa-remediation-20261016-ci-followup.md` y `amex-qa-remediation-20261016-ci-validation.md`. Nueva ejecución de GitHub pendiente. Sin migración, recuperación ni despliegue productivo.

## Corrección de revisión de concurrencia — 2026-10-16

PR #491 pasó todos los controles en `7ea82f2e1` (5595 unitarias,464 integración con5 skips heredados,91 navegador y94% Diff Coverage). La revisión pendiente detectó consultas SAT seleccionadas duplicadas por envíos simultáneos. Se corrigió con reserva temporal persistida y token de intento, compartiendo exclusión con el lote y liberando sólo la reserva propia. Contrato ya aprobado, sin nueva autoridad/esquema/dependencia. Validación final local: **846 pruebas PASS**, cobertura **630/679 (92.78%)**, revisión independiente **CLEAN_WITH_NOTES** sin hallazgos. Las40 huellas originales quedan como históricas:37 iguales y3 diferencias esperadas. Véanse los reportes `amex-qa-remediation-20261016-selected-sat-review.md` y `amex-qa-remediation-20261016-selected-sat-validation.md`. Nuevos checks GitHub pendientes en el commit corregido; sin efectos productivos.
