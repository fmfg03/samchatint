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


## PR489 review correction: explicit direct-workflow denial

The first published commit ee13e55f1 correctly fixed Odilon and the normal navigation destination. GitHub review thread PRRT_kwDORuojeM6s3wd4 identified a real boundary missed by initial validation: legacy AMEX visibility could still render a direct link for an actor explicitly denied amex_direct/ver. This follow-up enforces the already approved authority invariant and does not require a new business rule or scope approval.

A shared destination helper uses active Finance identity and the existing route-delegation check for GET. Missing explicit allow remains eligible for the real direct owner; empty default_roles is never misused as a denial. Explicit read denial or failed authorization lookup retains the previous statement destination and its original access checks. Both generic entrypoints call the same helper. No allow rule, role, scope, config or financial authority changes.

Reproduction against the exact immutable published head with actual persisted PostgreSQL AccessControlRule:1failed/1passed, `/tmp/amex-pr489-direct-denial-pg-red-boundary.log`; denied case incorrectly pointed direct. The first trial failed earlier because unrelated Direction position tables are absent in the AMEX fixture; the final boundary test isolates only Direction/approval helpers, leaving real access rules and AMEX owner/rendering untouched. That harness setup failure was caused by test preparation and is superseded. Worker unit red8failed then green159affected cases; report `/tmp/amex-pr489-direct-entry-denial-fix.md`.

Final frozen-source checks: **832unit PASS25.97s**, **35PostgreSQL PASS83.63s**, **9inventory PASS22.61s**; logs `/tmp/amex-pr489-review-final-{unit,pg,inventory}.log`. Two additional PostgreSQL cases verify actual rule denial and no-rule behavior through both real rendered handlers. Coverage against original base929fe1b47: **35/35=100%** (13entry-helper statements and22attention-policy statements), using only final fresh unit/PG files. Script and receipt `/tmp/amex-pr489-review-changed-coverage.py` and `.json`. New PG/entry tests format/lint and diff hygiene pass; no broad formatting of the legacy owner. Inventory refreshed for changed source hash/line positions, topology and permissions unchanged.

Initial CI results on ee13e55f1 are historical evidence only; the follow-up commit must pass its own checks before merge. Read-only independent revalidation follows. User authorization remains valid for this correction within the same approved final change and PR. No production effects have yet occurred for PR489. Canon unchanged for the same approved identity/navigation/authority contract.


---

# Independent incremental validation — PR489 persisted direct-read denial

Date: 2026-10-15. Read-only review of `/root/samchat/.worktrees/amex-odilon-linked-beneficiary-20261015`, incremental working diff against published head `ee13e55f1e9a06cdf0ae640b1b1c35a4fa3b598d`, with original approved Odilón/AMEX scope preserved.

## Critical findings

None.

## Important findings

None.

## Minor findings

None.

## Evidence and conclusion

Independently reviewed shared `_amex_entry_destination`, both real UI callsites, canonical `can_defer_path_authorization_to_route_guard`, `explicit_tool_decision` and `_load_rule`, actual direct route Finance predicate, unit and PostgreSQL regression tests, prior-head failure and final green receipts. The previously bootstrapped canons remain unchanged and their hashes still match the register.

The helper first requires active real Finance identity, then uses the existing canonical GET/ver delegation helper. Explicit persisted direct-read denial or authority lookup failure selects the existing legacy statement route; absent explicit allow preserves route-owned authorization rather than incorrectly treating intentionally empty default roles as a blanket denial. Real Finance/scope/action checks remain owned by the direct endpoint. Both generic entries retain inherited legacy visibility controls. Ineligible actors never obtain a direct destination, regardless of visibility rules. All destination strings are fixed internal paths. No new permission, context scope, redirect, financial effect, data repair, dependency, schema change or configuration mutation was introduced.

The PostgreSQL regression writes only synthetic AccessControlRule fixtures, then runs actual panel and batch handlers inside a READ ONLY transaction with real access rule reads. The two boundary cases show no explicit rule -> direct and explicit denial -> legacy while preserving reconciliation/form links. Only unrelated Direction/approval-position lookups are stubbed because those catalogs are outside the AMEX fixture. Published-head red receipt proves the denied case fails and permitted case passes; final implementation passes both. Eight handler-render decision cases additionally cover False/None/True/lookup-error for both entrypoints through real rule lookup logic; four inactive/non-Finance cases ensure no authority lookup occurs.

Final verification receipts independently read: 832 unit tests passed (25.97s), 35 isolated PostgreSQL tests passed (83.63s), changed executable coverage 35/35 = 100% (13 helper +22 existing policy). Coordinator reports nine inventory tests passed (22.61s). New tests' Black/isort/flake8 checks passed, and working diff whitespace check passed. No current source findings remain from thread `PRRT_kwDORuojeM6s3wd4`.

## Residual risks

Remote CI must pass the exact follow-up commit before protected merge. Publication, deployment, runtime screen verification and authenticated Finance UAT remain separate evidence. Selecting the legacy route intentionally preserves its own authority checks; the helper never grants access to either endpoint. Existing direct company/project/edition configuration remains validated by the direct owner after navigation. Existing warnings and unrelated repository lint/type debt are not claimed resolved.

Canon unchanged: this corrects navigation to respect the already approved direct workflow authority boundary. Review completed without source, test, Git, PR, database, configuration or production changes; only this `/tmp` validation artifact was written. Verdict: no blocking findings.
