# AMEX single-screen workspace — build report

2026-10-23. Base: f15e9a47650913097cfb63b2859dfe76d17e7f1c. User approved the three-stage proposal and explicitly authorized work through deployment. Story/spec and this report preserve that scope; no repeated permission checkpoint is required. This document records local verification before the separate GitHub/production receipts.

## Completed behavior

The same direct-state document now contains upload/inline original-sign preview, the central charges workspace and policy/download outcome. Intake adopts the newly created state's canonical URL through history replacement. Subsequent CFDI, treatment, NoCFDI, classification, budget handoff and policy review happen in one native dialog, with a lateral CFDI layout. Native server handlers/links remain available when opened directly; no complete no-JS workflow is claimed because the baseline already requires JS for preview and selection.

Filters, visible-row selection, explicitly retained hidden selections, scroll and drafts survive supported operations. Financial mutation payloads retain original FormData, repeated IDs, authority, CSRF, versions, identities, preview/source proofs, explicit confirmation and idempotency. Read generations and draft epochs reject late reads; POST is not aborted or retried automatically. Pending controls are locked, including the original form intentionally detached during preview, and restored only if previously enabled. Errors preserve context and require fresh server evidence; confirmations remain unchecked after refresh.

Juan Pablo's queue and authority remain separate. Foreground-only, nonoverlapping 20-second polling requests only source_version from the existing authorized statement GET before any suggestion/options/accounting work. Full content refresh occurs after a version change and pauses while editing, hidden or in a dialog. The UI reports actual handoff and owner diagnostics; absence of an individual issue is labelled accordingly and never grants posting readiness. Only the accounting owner's complete ready state enables confirmation. Frozen recognition/exports retain existing receipts.

No financial owner/model/schema, installed administrative/viaticos catalog, Daniel audit identity, actor privileges, dependencies, background jobs, matching policy, fiscal policy, signs or payment owner changed. Canon unchanged: this is the unified presentation of the already governed direct-state workflow; owners, authority, confirmations and immutable whole-cut rules remain intact.

## Changed files

- Presentation: `src/devnous/gastos/routes/admin_amex_direct_routes.py` and new `routes/amex_workspace_ui.py`.
- Native assets: `static/amex_workspace.js` and `static/amex_workspace.css`, content-versioned and served by the existing mount. Shell color/spacing tokens are reused.
- Tests: new helper/route units and single-screen browser matrix; existing direct/treatment browser tests and affected fixture static mounts; existing actual-owner browser/PostgreSQL journeys for CFDI and NoCFDI.
- Generated route inventory/matrix: regenerated with the existing tool. Same 609 routes, 574 live-static entries and 44 canonical actions; source-file count 478→479 reflects the pure presentation helper. No action/registration/authority expansion.
- Docs: approved story/spec, independent acceptance/validation reports and this build report.

## Verification

| Check | Result | Evidence |
|---|---|---|
| Affected HTTP and UI helper units, with coverage | 246 passed, 1 inherited warning, 54.99s | `/tmp/amex-single-screen-http-unit-final-20261023.log` |
| Focused workspace HTTP/browser, frozen functional JS | 36 passed, zero skipped, 29.41s | `/tmp/amex-single-screen-browser-final-20261023.log` |
| Actual PostgreSQL + real browser, two actors | 2 passed, zero skipped, 37.22s | `/tmp/amex-single-screen-pg-browser-final-20261023.log` |
| Final narrow mobile journal CSS regression | 1 passed; unrelated selection deselected | Acceptance report; final CSS fingerprint below |
| Changed executable Python lines | 123/124 = 99.19%; router48/49, helper75/75 | `/tmp/amex-single-screen-root-unit-coverage-20261023.json`; independent diff-line calculation |
| Black/isort; scoped Flake8 F/E9; JS syntax; Python compile; diff hygiene | Passed | Existing runtime tools and `node --check` |
| Runtime/package, registration surface, accepted regressions and workflow contract | Passed | Existing repository check scripts |
| Generated inventory tests | 9 passed in 22.91s | `/tmp/amex-single-screen-inventory-20261023.log` |

Counts overlap and must not be summed as unique tests. Browser tests use synthetic identities and owner boundaries; the two browser/PostgreSQL tests execute real financial owners and parse exact XLSX/CSV/ZIP output. They verify one main-document navigation, upload URL adoption, legitimate separate CP intervention, readonly automatic advancement, balanced policy amounts and frozen exports. No production charge was classified or policy generated as QA.

Desktop/mobile coverage includes 100 rows, selection across filters/refresh, drawer search/pagination/SAT consent/link, benefit without project, partner excluded from budget, NoCFDI bulk declaration/revocation, project assignment, form cancellation/focus/Escape, double POST prevention, JSON403/409/422, login HTML denial, authoritative303 errors, fresh nonce/version/source-key review and delayed reads while typing. Six screenshots: `/tmp/amex-single-screen-final-20261023/{main,drawer,journal}-{1440,390}.png`.

The final JS hash is `0b91d57a6d08746e5191d093bbca1e5526e600af5b45bf01be6708d665e3c916`. The final CSS hash is `c6937e1c74d84dbf11b1e109a38c60c874b3e89c0bdf71e125a2ac71c4eb299b`. The 36+2 functional checks used the preceding CSS (cfbfd...b485). The only subsequent delta is a 471-byte journal-only CSS block keeping four monetary cells unbroken and the table bounded within the mobile dialog. Its committed targeted browser regression passed separately and the updated screenshot was inspected. No Python, DOM, JS or financial value changed for that visual correction.

## Commands

- Scoped `python -m pytest` for HTTP/helper, browser and separate asynchronous real-PG browser processes, using the existing test venv and isolated PostgreSQL15 at localhost55471.
- `python -m black --check`, `python -m isort --check-only`, `python -m flake8 --select=F,E9`, `node --check static/amex_workspace.js`, `python -m compileall`, `git diff --check`.
- Scoped `python -m mypy --follow-imports=silent --ignore-missing-imports`, candidate and serving-base snapshots with independent temporary caches.
- `scripts/private_plugin/inventory.py`, `scripts/ci/check-pr-quality-gate.py`, `scripts/check_runtime_packaging_consistency.py`, `scripts/ci/check-registration-operational-surface.py`, `scripts/ci/check-accepted-regressions.py` and focused inventory pytest.

## Failures, corrections and residuals

Change-caused defects were corrected: relative canonical URL prevented fetch interception; a followed303 could discard a failed draft; late reads could overwrite post-dispatch edits; temporarily detached preview forms retained pending disabled state. Independent review identified the latter two, and committed browser regressions cover them. One initial helper/routes assertion expected the original project details closed; renderer was restored to that default, while an explicit project opener expands it. Initial test-synchronization and download-navigation instrumentation issues were corrected without weakening financial totals, links, owner receipt, budget actual or export assertions. PG assertions for copied budget IDs now reflect that only classified eligible rows may be submitted.

Mypy reports **10 identical pre-existing router errors** in candidate and serving base; line-normalized messages/codes match exactly. The new helper has no type errors. Receipts: `/tmp/amex-single-screen-mypy-{candidate,baseline}-20261023.log`. Do not describe this as globally clean typing. Existing setuptools/SQLAlchemy deprecation warnings remain distinct from runtime failures. No source/baseline change masks those findings.

Independent validator found no remaining Critical/Important/Minor issue and proved the exact multiset of all 61 financial/transaction calls unchanged against the approved base. Source/acceptance/coverage review is complete locally; required GitHub CI, release promotion and production checks require subsequent receipts. No schema migration or configuration/grant edit is planned. Preserve the current serving release and active catalog on promotion, retain both frontend bundles, backup and compare financial evidence, and verify actual source SHA/service/drop-in/health/readiness/journal afterward.

There is no single new real-PG browser case combining benefit, partner and project in one statement; treatment UI scenarios and the already verified financial treatment suite cover those owners separately. Numeric browser-JS coverage is not claimed; behavior is covered by the specified matrix. Authenticated Daniel/Juan Pablo business UAT and external COI import acceptance remain separate from synthetic acceptance and technical deployment.

## 2026-10-24 review follow-up

The initial head passed all GitHub gates, including integration and changed-code coverage. Merge remained blocked by two unresolved automated review threads, rather than failing CI. Both corrections stay inside the approved presentation scope: `request()` reports a successful root replacement explicitly, so polling preserves errors and cancellation instead of claiming an update; the 409 recovery button captures the current draft when clicked while retaining its authoritative recovery URL/key. No owner, payload, Python, schema or configuration changed. Canon unchanged for the same reason above.

Final JS SHA-256 supersedes the earlier receipt: `84802af2750d6da42a8c33dffb3c2a4c598b364136bde55fda8c476840d60902`. CSS and Python remain unchanged. Targeted browser coverage includes successful polling, network failure, HTTP error, response-context rejection, a concurrent edit cancelling a late read, and an edit made after a 409 before requesting recovery. Independent read-only review found no Critical/Important/Minor finding. Final browser result and publication/runtime evidence are recorded in the following acceptance and operational receipts; no production UAT is claimed.
