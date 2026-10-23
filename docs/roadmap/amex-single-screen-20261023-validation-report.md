# AMEX single-screen implementation validation

Date: 2026-10-23. Independent read-only implementation review against approved story/spec, base `f15e9a47650913097cfb63b2859dfe76d17e7f1c`. Only this findings report was authored by the validator; no implementation or test files were edited.

## Critical findings

None discovered.

## Important findings

None remain open. Two findings were returned to build and revalidated:

1. Slow GET responses could replace edits made after dispatch. The corrected client checks draft epoch and request generation before mounting HTML; business input cancels only pending reads. Final desktop/mobile delayed-read typing regressions pass.
2. POST preview could leave a detached original dialog source disabled after close/reopen. The corrected client restores captured previously enabled controls only when connected or contained in the exact retained source; permission/server-disabled controls remain excluded. Final preview-close-reopen and pending-input-lock regressions pass.

## Minor findings

None discovered.

## Review evidence

- Read the three mandatory canons in their required order; SHA-256 matches the convergence register: product `4f369621a03290d320c9b9efbd5f16683bcfb6e50d99bc5bae1e3d8e7d45345e`, engineering `19f670adb418ac2f359f793cde5f32d351686dd2fa6874ddc7ad601dc0f7305e`, sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`.
- Approved change remains presentation-only: root router rendering, new pure UI helper, two static assets, scoped fixtures/acceptance tests and roadmap evidence. No financial service/model/schema/dependency/configuration change discovered.
- Independent AST comparison of financial and transaction calls in the router: exact equality, 61 baseline calls and 61 candidate calls, including arguments. Authority, CSRF, source versions, confirmation, nonce/idempotency and commit/rollback paths remain owned by existing handlers/services.
- Poll reuses the authorized statement GET and returns source version before classification options, suggestions or accounting preview. Client polling pauses on hidden document, open panel, writing, reading or dirty draft.
- Generated route inventory has identical route identities; only line/source hash/derived ID/tool name and source-file count change. Actions, access-profile catalog and registrations remain identical.
- Same-origin/same-import interception, checked response context and controlled fragment extraction preserve scope. Returned script/style/link elements are removed; messages use textContent. Download and CP navigation remain separate.
- Source-local disabled fieldsets survive modal movement. Frozen controls stay disabled. Selection derives existing eligible row IDs; permission checks remain server-side.
- Negative owner issues never manufacture positive row readiness; only owner-ready complete states receive ready labels. Cut/completion remain distinct. Full blockers are retained.
- Error recovery restores only visible draft fields, never hidden source proofs/nonces; confirmation is unchecked. Pending redirects carry error_msg without losing original msg. Writes do not automatically retry.
- Frozen-source final browser receipt (36/36) covers delayed read plus typing without draft loss, pending journal fresh nonce/source key/version with preserved date/reason and unchecked confirmation, redirected 303 fresh-source recovery, and JSON 409 explicit GET recovery. Tested JS/router hashes exactly match this report. The 36+2 runs used the earlier CSS hash cfbfdcefbf035020b65b3869215f6c21e82eddd7576e16acbe59b74171d5b485; the subsequent CSS-only amendment is documented below.
- Actual shell tokens verified against `_workspace_shell_styles`; native dialog title, focus return, Escape and pending-write controls inspected.
- `node --check static/amex_workspace.js` and `git diff --check` pass. An initial AST command used unavailable `python`; rerun with `python3` succeeded (environment-only).
- Acceptance verifier reported 265 passing checks (236 HTTP, 27 browser, 2 isolated real PostgreSQL/browser), zero skips, before the final frontend corrections. Those earlier receipts are superseded by the final frozen-source runs below. Root unit run initially had 187 pass/1 failed obsolete details-open expectation; coordinator restored closed source. Final HTTP/unit rerun is green: 246 passed, 1 warning (`/tmp/amex-single-screen-http-unit-final-20261023.log`). Coverage JSON proves helper 75/75 statements (100%) and independent changed-line calculation for router 48/49 executable added lines (97.96%; unexecuted line 2088 is the pending recovery redirect).

## Residual risks

- Final frozen-source sync routes/browser acceptance: 36 passed, zero skips, 29.41s (`/tmp/amex-single-screen-browser-final-20261023.log`). Real PostgreSQL/browser: 2 passed, zero skips, 37.22s (`/tmp/amex-single-screen-pg-browser-final-20261023.log`). Independently inspected logs and rechecked source hashes. Versioned acceptance report: `docs/roadmap/amex-single-screen-20261023-acceptance-report.md`.
- Scoped implementation validation is complete with no open findings. This pass does not assert that whole-repository lint/typecheck/build/CI checks passed; coordinator retains those separate required gates and any preexisting debt classification.
- Real PostgreSQL/browser paths cover full CFDI and Sin CFDI journeys and actual parsed XLSX/CSV/ZIP. Benefit/partner/project controls have separate browser coverage; no single real PostgreSQL/browser journey mixes every treatment.
- Isolated synthetic acceptance is not Daniel authenticated UAT, COI importer acceptance or production deployment. Release health, source reconciliation, runtime smoke and Finance acceptance retain separate evidence.
- Canon unchanged: unified presentation retains existing financial owners, authorities, source/version controls, immutable complete cut and payment separation; no new normative rule.

## Reviewed implementation fingerprints

| File | SHA-256 |
| --- | --- |
| src/devnous/gastos/routes/admin_amex_direct_routes.py | 6e1fa2bf65fd3ea04232be10c0cf1ad16e7abb29a3ce566cbf4ee9332dd47d3e |
| src/devnous/gastos/routes/amex_workspace_ui.py | 7d3c0182a320c7aa48ee21336d72117bfd4f1482127dce8631e075eb89629b90 |
| static/amex_workspace.js | 0b91d57a6d08746e5191d093bbca1e5526e600af5b45bf01be6708d665e3c916 |
| static/amex_workspace.css | c6937e1c74d84dbf11b1e109a38c60c874b3e89c0bdf71e125a2ac71c4eb299b |

## Narrow visual amendment after the 36+2 receipt

A real mobile journal visual review found an amount wrapping as `116.0` plus a final `0` on the next line. The 471-byte CSS-only correction scopes nowrap, normal word breaking, right alignment and tabular numbers to journal monetary columns 3/4 and bounds horizontal overflow inside the journal. Validator verified the entire original CSS prefix hashes exactly to the prior cfbfdcefbf035020b65b3869215f6c21e82eddd7576e16acbe59b74171d5b485. JavaScript/Python/DOM/financial calls are unchanged. New CSS hash is c6937e1c74d84dbf11b1e109a38c60c874b3e89c0bdf71e125a2ac71c4eb299b. No new scope or authority issue discovered. Targeted versioned browser regression `test_mobile_journal_keeps_monetary_cells_on_one_line` passed: 1 passed, 17 deselected, 4.00s (`/tmp/amex-single-screen-mobile-journal-css-final-20261023.log`). Validator inspected the test and log: all four monetary strings stay exact, one text rectangle per cell, nowrap/right/tabular styles, table bounded by dialog, no document overflow, no commit. Validator also viewed the corrected `journal-390.png`: 116.00 amounts are intact and readable. No open findings for the final CSS; the earlier 36+2 runs are not claimed to have used this later CSS.

Coordinator reports source Black/isort/flake8 F,E9 gates passed and mypy has the same 10 findings as the baseline, with no helper findings. This read-only validation does not claim a clean whole-repository typecheck.
