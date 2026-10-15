# AMEX: consistent linked beneficiary and direct entry navigation

Date:2026-10-15. Base929fe1b4729f07b6b486e9a7eede966f827169de. Scope resumes the approved AMEX attention story/spec (`amex-statement-workflow-20261013-story.md`, `-spec.md`) and the approved direct workflow. User explicitly requested generic AMEX buttons open the new module while reporting Odilon still visible at `/admin/gastos/amex/conciliacion?return_to=/admin/contabilidad/amex/estados/1bdabdf2-7b47-4964-ad1f-210e93040b95/pendientes&year=2026&month=9`.

## Problem and accepted behavior

Odilon's report/items must leave manual AMEX discovery/selectors/counts; actual bank charges remain visible. Explicitly contradictory, missing or alternate beneficiaries remain visible. The primary AMEX entry must open `/admin/contabilidad/amex/estados/directo`; existing report/history/payment actions retain their destinations and authority. No new accounting source, grant, migration, data repair or dependency.

## Evidence and technical correction

A read-only replay of the actual September selector returned61 candidate report expenses;60 from six Odilon accounts stayed visible with `amex_attention_ambiguous_beneficiary`. Every affected account/report had Odilon's explicit employee UUID plus provider UUIDc79ed5bd-4dec-4343-908d-d714937eaa50, whose persisted `proveedores_clientes.empleado_id` points to the same Odilon UUID. No alternate discriminator existed. The filter treated two representations of the same evidenced party as a conflict. Identity was verified by UUID/FK, not names, capturer, card or matching amounts.

The shared attention owner now batch-loads provider→employee links only for referenced beneficiaries. A dual employee/provider resolves to employee solely on exact persisted UUID equality and no alternate discriminator. Default empty evidence preserves conservative classification. Provider-only behavior, real contradictions, malformed/missing links, multiple/missing reports and bank bypass stay intact. No beneficiary values are rewritten. All existing queues use this shared owner.

Two generic UI entries change: the AMEX card in `/panel` and the batch page's primary `Abrir recorrido AMEX` button. Panel text now describes state/CFDI/card policy. Existing permissions and specific reconciliation/payment/report/history URLs remain unchanged; no blanket redirect. Contabilidad subnavigation has no AMEX entry requiring modification.

Canon unchanged: corrects the already approved explicit-beneficiary compatibility and direct-workflow entry without changing product rules, authority, accounting, schema or architecture. All three canon hashes match the register. Generated route inventory is refreshed because the route-owner source hash changed; route matrix and routing topology remain unchanged.

## Files

- `src/devnous/gastos/services/amex_attention_policy.py`
- `src/devnous/gastos/routes/user_routes.py` (two destination strings and one description only)
- `tests/unit/gastos/test_amex_attention_policy.py` (16 additional identity cases)
- `tests/unit/gastos/test_amex_direct_navigation.py` (three actual rendered-handler cases)
- `tests/unit/gastos/test_documentos_todos_reporting.py` (two expected route/copy assertions)
- `tests/integration/test_amex_linked_beneficiary_attention_postgres.py` (four real PostgreSQL variants across shared policy, actual reconciliation selector, statement workflow and accounting report discovery)
- generated `docs/private-plugin/route-inventory.json`; this report.

## Verification

- Real production read-only red: `/tmp/amex-odilon-september-before.json`,61 visible/60Odilon. Corrected same-row read-only replay: `/tmp/amex-odilon-september-corrected-readonly.log`,1 visible/0Odilon. No business writes or SAT calls.
- Actual PostgreSQL baseline run using deployed old source: `/tmp/amex-odilon-pg-red.log`,1failed/1passed/2deselected. Exact dual-party case fails expected exclusion; alternate-party case remains visible.
- New unit tests initially16failed/21passed due missing linked-evidence API, then37passed. Baseline classifier replay independently fails correct exclusion assertion. Worker receipt `/tmp/amex-odilon-policy-worker-report.md`.
- Navigation red2failed/1passed, green5passed; complete affected UI modules119passed. Worker receipt `/tmp/amex-odilon-ui-navigation-report.md`.
- Final combined units: **820passed**,26.67s, `/tmp/amex-odilon-final-unit.log`.
- Final PostgreSQL: **33passed**,80.78s, `/tmp/amex-odilon-final-pg.log`; real owners/SQL, synthetic loopback55471 database only.
- Inventory reproducibility: **9passed**,22.35s, `/tmp/amex-odilon-inventory-tests.log`.
- Changed executable coverage against base929fe1b47: **22/22=100%**, measured with Coverage API/difflib using final unit+PG inputs only. `/tmp/amex-odilon-changed-coverage.py` and `.json`. Navigation string replacements have no independently changed executable statement; actual rendered-handler tests verify their behavior.
- Black/isort/Flake8(max88) of changed/new focused files pass; policy Mypy pass; shared monolithic user_routes is not reformatted or claimed globally lint/type clean. Working diff check passes.

Commands use `PYTHONPATH=src:.:/tmp/samchat-amex-source-testdeps`, `/root/samchat/.venv/bin/python -m pytest -o addopts=''`. Unit command explicitly restores `-o asyncio_mode=auto` because repository pytest.ini normally sets auto through addopts; clearing addopts otherwise makes an inherited unmarked async test fail in strict mode. Full units include test_amex*.py, test_documentos_todos_reporting.py and test_navigation_inventory_contract.py. PG includes the new file, test_amex_statement_workflow_postgres.py, and the historical real-card selector. Coverage uses module targets `devnous.gastos.services.amex_attention_policy` and `devnous.gastos.routes.user_routes`.

## Failed or superseded checks

An initial combined PG collection failed with KeyError while the new fixture imports preceded runtime-package initialization after isort. Reestablishing explicit runtime bootstrap before reusing legacy fixture functions resolved collection; no production source change was required for it. The full33-pass run supersedes this change-caused harness failure. An initial coverage calculation ran before the PG process finished and saw unit-only81.82%; it is superseded by the completed-input100% result, not a new scope exception. No required final check remains failed. Strict-mode UI harness failure above is caused by overriding repository auto-mode configuration, not the href change.

## Release boundary and remaining risk

This candidate has not been committed, pushed, merged or deployed. Existing release929fe1b47 remains live with its production fiscal/access configuration preserved. Final diff approval is a separate checkpoint. No production data repair, beneficiary cleanup, financial status update, real statement import, SAT request, budget assignment, journal posting or COI/payment effect was executed. Authenticated operational acceptance remains pending after an authorized release. Policy correction rollback is code-only; no migration required.


---

# Independent read-only validation — AMEX Odilón and navigation correction

Date: 2026-10-15. Worktree: `/root/samchat/.worktrees/amex-odilon-linked-beneficiary-20261015`. Base: `929fe1b4729f07b6b486e9a7eede966f827169de`.

## Critical findings

None.

## Important findings

None.

## Minor findings

None.

## Evidence and scope

Read the three mandatory canons in required order and independently matched all SHA-256 values against the convergence register. Reviewed the original approved `amex-statement-workflow-20261013-story.md` and `-spec.md`, source diff, changed tests, real PostgreSQL regression, root verification logs and coverage receipt.

The policy change corrects classification of one explicitly persisted beneficiary represented simultaneously by employee and provider UUIDs. Resolution requires exact equality between the explicit employee UUID and the referenced provider row's persisted `empleado_id`; any alternate discriminator, absent/foreign link, conflicting document/account identity, unknown beneficiary or multiple candidate INFORMEs remains visible. Provider-only identity is deliberately unchanged. There is no fallback to capturer, approver, name, RFC, amount, card or inferred ownership. The additional read is batched and restricted to referenced provider UUIDs, inside the existing no-autoflush boundary. Existing bank-row visibility bypass is preserved. Shared callsites continue to govern reconciliation selectors, report discovery and statement workflow; no financial owner, document state, amount, cut, journal, permission or data-repair path changed.

The two generic UI entry links point to the already existing `/admin/contabilidad/amex/estados/directo` destination. Actual handler-render tests cover inherited tool visibility, destination and preserved legacy reconciliation/form actions. No global redirect, new route, payment/history rewrite or permission expansion was introduced.

Fresh final evidence: 820 unit tests passed (26.67s); 33 real isolated PostgreSQL tests passed (80.78s), including all four exact-link/conflict/alternate regressions plus statement workflow and real legacy selector. The PostgreSQL regression opens a read-only transaction around actual queue handlers and asserts bank visibility, absence of new journals and unchanged source identity/state. Root reports 9 inventory tests passed. Changed executable policy coverage is 22/22 = 100%; route destination strings are not independently executable lines, and are verified through handler output tests. Scoped policy mypy and new-test formatting/lint passed. `git diff --check` passed. Inventory diff reflects source hash regeneration rather than route topology changes.

The exact old deployed code fails the matching dual-ID PostgreSQL case; corrected code passes. Supplied same-data production read-only receipts report September candidates 61 -> 1 and Odilón report items 60 -> 0; this is investigation evidence, not a deployed correction or business acceptance. No production reads or writes were performed by this validator.

## Residual risks

Full remote CI, final approval/publication, deployment and authenticated Finance UAT remain separate checkpoints. The user-visible production incident remains open until an authorized exact release is deployed and the reported screen is rechecked. Source beneficiary links are read at query time; genuinely missing or contradictory records intentionally continue to appear. No migrations or business data repairs are required or included.

An initial grouped PostgreSQL collection failed with `KeyError: test_amex_shared_recognition_postgres`; the new test now initializes the real runtime package before importing the existing fixture chain, and the same grouped suite passes. No production-source change was used to address that test bootstrap issue. Existing deprecation/schema-cycle warnings remain; no broad lint/type cleanup is claimed. An early coverage calculation performed before PostgreSQL completion is superseded by the complete 100% receipt.

Canon unchanged: this restores the already approved explicit-beneficiary queue rule and connects the existing approved direct module; it adds no product authority, architectural pattern or financial behavior.

Verdict: no blocking findings discovered in the reviewed correction. Read-only review completed; no source, test, Git, database, configuration or production changes performed by this validator. Only this validation artifact was written.


## Promotion authorization

The user answered “aprobado” to the explicit final question approving this exact correction and authorizing commit, push, PR, merge and production deployment. Story/spec and reviewed source scope are unchanged. The test container was stopped after verification. Origin/main and the active serving release were both independently reverified at929fe1b4729f07b6b486e9a7eede966f827169de before publication. No migration or financial/data repair is needed. Runtime configuration of Plataforma Sports, five cards, four Finance operators and Juan Pablo CP must be preserved during deployment. CI, exact merge and serving receipts will record actual promotion results separately; approval does not turn local checks into remote or business acceptance.
