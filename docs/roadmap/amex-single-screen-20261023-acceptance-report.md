# AMEX single-screen acceptance verification

Date: 2026-10-23. Approved scope: `amex-single-screen-20261023-story.md` and `amex-single-screen-20261023-spec.md`. Verification ran in the isolated worktree `/root/samchat/.worktrees/amex-single-screen-20261023` against synthetic identities and an isolated PostgreSQL 15 database on localhost:55471. Financial services, budget assignment, journal creation and export remain actual owners in the PostgreSQL browser journeys. Identity/session injection and external SAT transport are the explicit test boundaries.

## Final result and frozen source

Final focused acceptance: **36 passed**, zero failures/skips, in 29.41 seconds. This includes four workspace HTTP tests and 32 browser cases across desktop and mobile. Final real PostgreSQL plus Chromium journeys: **2 passed**, zero failures/skips, in 37.22 seconds. Root's separate final affected HTTP/helper run reports **246 passed**; it overlaps the workspace HTTP checks and is not an additional unique-case total.

These results apply to the following SHA-256 fingerprints, checked before and after the final runs:

| Source | SHA-256 |
| --- | --- |
| `src/devnous/gastos/routes/admin_amex_direct_routes.py` | `6e1fa2bf65fd3ea04232be10c0cf1ad16e7abb29a3ce566cbf4ee9332dd47d3e` |
| `src/devnous/gastos/routes/amex_workspace_ui.py` | `7d3c0182a320c7aa48ee21336d72117bfd4f1482127dce8631e075eb89629b90` |
| `static/amex_workspace.js` | `0b91d57a6d08746e5191d093bbca1e5526e600af5b45bf01be6708d665e3c916` |
| `static/amex_workspace.css` | `cfbfdcefbf035020b65b3869215f6c21e82eddd7576e16acbe59b74171d5b485` |

Final receipts: `/tmp/amex-single-screen-browser-final-20261023.log`, `/tmp/amex-single-screen-pg-browser-final-20261023.log`; separate root HTTP/helper receipt: `/tmp/amex-single-screen-http-unit-final-20261023.log`. Earlier failures and passing runs on prior JavaScript fingerprints were diagnostic evidence and are superseded by these final receipts.

## Acceptance mapping

| Acceptance criterion | Coverage method | Result | Notes or gaps |
| --- | --- | --- | --- |
| 1. Explicit intake and canonical URL without document reload | Existing direct browser workflow at desktop/mobile; actual PostgreSQL intake journey | PASS | Original file/sign preview, sign-change invalidation, confirmation and canonical identity adoption. The real journey asserts one main-document request excluding downloads and preserves an identity placed on the original `document`; `history.replaceState` and download requests do not masquerade as reloads. |
| 2. Central table, filters, eligible selection and position | 100-row synthetic browser fixture; repeated refresh and FormData assertions | PASS | Search, selection by IDs, eligible selected forms, scroll preservation; project checkboxes retain external form association. Fresh hidden versions are checked separately rather than restored from drafts. |
| 3. CFDI suggestions, search, pagination, consent and linking in drawer | Actual rendered picker with synthetic read boundaries; full real PostgreSQL CFDI browser journey | PASS | Opaque pagination cursor, manual search, selected invoice/item/source versions, explicit SAT consent and actual financial linking. |
| 4. Treatment, bulk selection, withdrawal and Sin CFDI | Existing treatment HTTP regressions; desktop/mobile treatment browser; actual bulk Sin CFDI PostgreSQL journey | PASS across suites | Benefit/partner preview and confirmation, exact selected IDs, no budget submission for partner; actual no-CFDI declare/revoke/redeclare, source decisions and final cut/export. Treatment withdrawal remains covered by HTTP tests rather than a dedicated browser withdrawal journey. |
| 5. Benefit without project and partner without budget | Desktop/mobile treatment browser plus affected HTTP tests | PASS at UI boundary | Benefit requires neither project nor category in its treatment form; authorized partner account is explicit and partner rows are ineligible for budget. The real-PG browser journeys use project expenses; a combined real-PG treatment journey remains a gap below. |
| 6. CP separate authority and read-only foreground progress | Actual Finance and CP browser contexts against PostgreSQL | PASS | CP assigns actual lines on a separate page; Finance waits for the 20-second foreground poll to observe a changed version and asserts zero automatic Finance POSTs during the intervention. Cheap poll HTTP checks preserve owner authorization and avoid SAT/options/accounting preview/commit. |
| 7. Whole policy, immutable owner controls and downloads | Two actual PostgreSQL browser journeys and retained HTTP regressions | PASS | Complete balanced 348.00 journal, exact rendered preview, one policy, real recognition memberships, no fabricated INFORME, and parsed XLSX/CSV/ZIP. Frozen forms disappear; exports retain fiscal/no-CFDI evidence. |
| 8. Contextual errors, draft retention and fresh review | Browser matrix: 403/409/422 JSON, HTML login, followed 303 error, actual pending owner, slow GET, rapid item change and network abort | PASS | Escaped messages; no stale late read overwrites newer search/reason; no automatic POST retry. Actual pending journal result rolls back and refreshes source version/key/nonce while retaining visible date/reason and clearing confirmation. JSON409 requires an explicit read refresh before another submission. |
| 9. Accessible dialogs and pending writes | Desktop/mobile browser and explicit pending-control assertions | PASS within checked interactions | Title, contained focus, Escape/close, return focus, no mutation on cancel; pending date/reason/confirmation controls disabled; duplicate submit blocked; Escape cannot simulate cancellation of pending write. Preview close/reopen leaves original reason and submit controls editable. This is not a full assistive-technology audit. |
| 10. Technical browser-to-policy/Excel journey | Two real PostgreSQL plus Chromium paths, with synthetic business actors | PASS for the required real-owner minimum; combined mix partial | One path begins at upload and completes actual CFDI linking, project classification, CP intervention, policy and exports without main-document replacement. A second completes actual bulk Sin CFDI revocation/redeclaration, classification, CP, cut and exports. No single real-PG browser case combines benefit, partner and project treatments; those treatments are exercised separately at the browser/HTTP boundary. |

## Changed verification files

- `tests/unit/gastos/test_amex_workspace_routes.py`: wrappers, cheap authorized poll and pending-result redirect/rollback.
- `tests/browser/test_amex_single_screen_workflow.py`: acceptance error/race/dialog/volume/recovery matrix and screenshots.
- `tests/browser/test_amex_account_treatment_workflow.py`: in-screen treatment dialogs and authoritative conflict versions.
- `tests/browser/test_amex_direct_workflow.py`: in-screen intake and canonical URL checks.
- `tests/unit/gastos/test_amex_statement_routes.py`: actual `/static` fixture mount.
- `tests/acceptance/test_amex_qa_browser_accounting_journey.py`: actual static mount, fetch-aware waits, same-document journey and separate CP polling.
- `tests/acceptance/test_amex_no_cfdi_browser_journey.py`: dialog presentation and external form association; all financial assertions retained. Before classification, budget hidden selection is correctly zero because the unchanged owner eligibility requires `budget_status=classified`; after classification the existing two-item budget assertions remain.

The acceptance agent made no implementation edits or production mutations and installed no dependencies.

## Commands and environment

Python: `/root/samchat/.venv/bin/python`. Browser: existing Chromium executable `/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome`. Set `PYTHONPATH=src:.` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to that executable. Browser artifacts use `/tmp/amex-single-screen-browser`. PostgreSQL checks use `TEST_AMEX_DATABASE_URL` configured for the synthetic isolated localhost:55471 database; no production database is used.

```text
python -m pytest tests/unit/gastos/test_amex_workspace_routes.py tests/browser/test_amex_single_screen_workflow.py tests/browser/test_amex_account_treatment_workflow.py tests/browser/test_amex_direct_workflow.py -q --tb=short
python -m pytest tests/acceptance/test_amex_qa_browser_accounting_journey.py tests/acceptance/test_amex_no_cfdi_browser_journey.py -q --tb=short
```

Black `--check`, isort `--check-only` on the seven changed test files and `git diff --check` passed. Formatting uses the existing `/srv/samchat/venvs/release-598e7a286/bin/python` environment. Required broader lint/build/CI and release checks are reported by the coordinator; this acceptance report does not replace them.

## Visual evidence and limitations

Six synthetic screenshots are available under `/tmp/amex-single-screen-final-20261023/`: `main-1440.png`, `main-390.png`, `drawer-1440.png`, `drawer-390.png`, `journal-1440.png`, `journal-390.png`. Main captures are full-page; drawer/journal captures show their actual viewport. The journal visual fixture explicitly supplies a balanced synthetic 116.00 preview; real journal correctness is verified separately in PostgreSQL. Synthetic hostile strings remain visible as escaped text to demonstrate safe rendering.

Manual inspection checked the mobile main screen, desktop drawer and mobile journal. The shared platform/teal styling and dialog controls are visible. Mobile journal monetary cells can wrap across lines at 390px; totals remain fully readable. The movement table retains horizontal scrolling. Screenshots do not prove business acceptance or a complete accessibility audit.

No final test failures remain. Known warnings are existing SQLAlchemy declarative-base and UTC datetime deprecations. The combined real-PG benefit/partner/project browser mixture and actual assistive-technology usage remain unverified. Production promotion, authenticated Daniel/Finance UAT and external COI acceptance require their separate evidence; these isolated technical checks do not establish those states.

Canon unchanged: this verification covers the approved presentation unification and preserves financial owners, permissions, explicit confirmations, original evidence and immutable whole-statement cuts. It introduces no new normative business rule or historical production observation.

## CSS-only acceptance addendum: mobile monetary cells

After the preceding 36-case and two PostgreSQL journey receipts, a presentation-only CSS correction removed monetary-cell wrapping in the mobile journal. Final CSS SHA-256 is `c6937e1c74d84dbf11b1e109a38c60c874b3e89c0bdf71e125a2ac71c4eb299b`; JavaScript remains `0b91d57a6d08746e5191d093bbca1e5526e600af5b45bf01be6708d665e3c916`, and the Python fingerprints above are unchanged. The preceding 36+2 receipts describe the prior CSS fingerprint, not a rerun of those suites on this CSS-only delta.

A versioned browser regression, `test_mobile_journal_keeps_monetary_cells_on_one_line`, now checks a 390px viewport with the explicit balanced synthetic preview. All four debit/credit texts remain exactly `116.00`, `0.00`, `0.00`, `116.00`; a DOM Range yields one text rectangle per cell. Computed styles require nowrap, right alignment and tabular numerals. The table stays bounded by the dialog and the document has no horizontal overflow. No financial write is executed.

Targeted command: `python -m pytest tests/browser/test_amex_single_screen_workflow.py -k mobile_journal_keeps_monetary -q --tb=short`, with the same Python, source path and Chromium environment above. Result: **1 passed**, 17 unrelated cases deselected, in 4.00 seconds. Log: `/tmp/amex-single-screen-mobile-journal-css-final-20261023.log`. The corrected viewport capture replaces `/tmp/amex-single-screen-final-20261023/journal-390.png`. This closes the mobile monetary wrapping observation above. Black and isort checks pass for the changed browser test; no further financial-suite rerun is claimed for the CSS-only delta.

## 2026-10-24 review regressions

Two valid P2 review findings were corrected inside the presentation layer: a swallowed failed/cancelled polling refresh could claim success; a 409 recovery could restore the submission-time draft instead of subsequent edits. The existing 409 case now edits the reason after the conflict and verifies that exact latest value after fresh source/item versions arrive, with confirmation still unchecked. New polling cases verify success, network failure, HTTP503, context rejection and a concurrent edit cancelling a late response. They assert retained root/version/draft and no false success, with zero financial commits. The widened browser suite uses final JS `84802af2750d6da42a8c33dffb3c2a4c598b364136bde55fda8c476840d60902` and the unchanged final CSS. It does not repeat PostgreSQL financial journeys because owners and payloads are unchanged; the earlier two actual-owner receipts retain their original fingerprints.

Final command: `python -m pytest tests/browser/test_amex_single_screen_workflow.py tests/browser/test_amex_direct_workflow.py tests/browser/test_amex_account_treatment_workflow.py -q`. Result: **38 passed**, zero skipped, one inherited SQLAlchemy warning, 32.64 seconds. Log: `/tmp/amex502-browser-final-20261024.log`. Node syntax, Black/isort checks and diff hygiene passed. Updated-head GitHub CI and production verification remain separate.
