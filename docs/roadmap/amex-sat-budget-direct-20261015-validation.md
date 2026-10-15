# Independent implementation validation — AMEX direct

Date: 2026-10-15. Worktree `/root/samchat/.worktrees/amex-sat-budget-direct-20261015`; baseline `d8c9fda23ed1b1bb1a68a08bc524fedbdb6d4e5f`. Result: **no unresolved implementation findings after correction and read-only revalidation**. This validator changed no implementation or test files; this report is its only write.

Approved objects: `docs/roadmap/amex-sat-budget-direct-20261015-story.md` and `-spec.md`, including approval-continuity receipt. Canon bootstrap read in required product → engineering → sweep order. Current register hashes verified: product `7062756077f455741f03027b335f0be6627476c23f9f0b4ab8744f0a9608c99e`, engineering `bd011d2d33deaea655e4bc3f863e61610454520544d477e1cba1560f6fabd47c`, sweep `2c55cc807c464d5462bca3e9d092fcefe6f5f2f66f4498231d72a7b57d795985`. Exact amendments were explicitly approved. No new authority, dependencies, production operations or unapproved architecture introduced by the reviewed correction.

## Critical findings

None unresolved.

## Important findings

None unresolved. The three important issues identified during the first read were corrected and revalidated:

1. **Explicit open period:** direct confirmation now requires an actual `AccountingClosePeriod` row in `open` status under lock for the explicitly selected accounting date. Missing periods remain pending; no implicit creation. Legacy `period_reason` behavior remains unchanged. Missing-period PostgreSQL regression passes; existing closed-period and valid-open flows pass.
2. **Canonical fiscal withholdings:** deferred SQL distinguishes card-liability bank net from evidenced tax credits. Exact tax code/amount is checked against canonical CFDI tax data, account identity against the actual expense retention mapping and active account, and frozen movement evidence against posted lines. The direct transaction materializes the existing canonical resolver result into the existing override field only when necessary, audits `canonical_retention_mapping` as derived evidence, and does not invent a new mapping catalog. Real custom ENV mapping posts base100 + IVA16 against card106 + retention10, exports and replays. Injected cut failure after mapping/event flush rolls back the override, event, journal, consumption and cut; retry succeeds. SQL negatives reject a forged account even with jointly forged proof/frozen line, changed tax amount, or absent proof.
3. **Budget edition compatibility:** classification reuses the existing calendar-edition helper for the original economic date; confirmation checks the explicit accounting date against each new source's edition. SQL additionally checks economic/accounting dates and budget-version edition. Missing/mismatched editions fail closed. Prior recognized receipts are not reclassified or moved to another edition.

The defensive prior-coverage proof was aligned: new-source fiscal/budget requirements apply only to new coverage; legitimate prior coverage must preserve exact consumption, bank anchor, card, journal and its actual canonical receipt. New imports always create a new bank anchor. Similar CFDI/amount/date does not supply a canonical prior association and remains an exception. Same-anchor cut replay returns the existing cut without another journal. The review did not authorize or introduce a new direct/report association mechanism.

## Minor findings

None unresolved. The four new blocking reasons now have specific operator messages identifying the responsible function and required correction. Story/spec headings now show the actual 2026-10-15 approval instead of the superseded pending status.

## Residual risks

- These are isolated implementation and synthetic-verification results. Production owner migration, fiscal/card/entity configuration, real actor/project scopes, publication, deployment, authenticated Finance/CP UAT, real SAT transport, actual COI import and actual bank payment remain separate authorized gates. No production data was read or changed by this review.
- Existing quality debt remains precisely classified: Mypy49 errors in shared owners, matching baseline with no new/resolved errors; Flake8 three inherited findings. Consequently repository-wide lint/types are not claimed universally clean. The coordinator's final build report must retain these limitations.
- Real browser tests use Chromium and synthetic service boundaries; PostgreSQL tests use real owners/SQL with synthetic identities and stubbed SOAP transport. A new CSV that resembles previously recognized spending remains pending unless a real canonical identity exists; this is deliberate preservation of the no-inference rule, not a backfill of historical reports or the14 protected cases.
- CI/publication and production acceptance were not executed or approved by this validator. Read-only validation is not final human approval.

## Evidence checked

Read implementation and diff for strict signed intake/private original bytes, explicit actor/action/entity/card/CP project scopes, specific denials, CSRF and line-only CP payloads, append-only context/status/events, bounded server-owned SOAP continuation outside SQL sessions, canonical CFDI linking/global uniqueness, whole immutable cuts, truthful shared direct export without fabricated INFORME, current historical-assignment pointers, budget projection/ledger deduplication, owner-only schema admission and populated-history rollback refusal. No dependency manifests, service configuration or unrelated feature source changes found. `git diff --check` passed after corrections; all canon hashes still match register.

Reviewed final receipts (tests executed by independent acceptance/build lanes, not rerun by this validator):

- `/tmp/amex-direct-root-final.log`: **767 passed**,22.61s.
- `/tmp/amex-direct-backend-current-tests.log`: **132 passed**,144.97s; includes real PostgreSQL acceptance/backend cases and retention mapping failure/retry.
- `/tmp/samchat-amex-direct-schema-tax-prior-final-20261015.log`: **40 passed**,53.77s after final SQL/golden alignment.
- `/tmp/amex-direct-admission-final.log`: **48 passed**,4.06s.
- `/tmp/amex-direct-frontend-http-final.log`: **113 passed**,12.95s.
- `/tmp/amex-direct-browser-final.log`: **3 passed**,10.17s.
- `/tmp/amex-direct-inventory-tests.log`: **11 passed**,64.46s.
- `/tmp/amex-direct-changed-coverage.json`: **2008/2215 changed executable statements =90.65%**. Read calculation script and independently compared changed/untracked Python source scope with coverage file list: **25 expected,25 reported,0 unmeasured**. Input coverage comes only from final root/backend/browser runs, includes untracked implementation, and compares against the exact baseline without staging. Lanes overlap; their counts are not additive unique test totals.
- `/tmp/amex-direct-quality-comparison.json`: baseline49/current49, no new type errors.

Earlier `/tmp/amex-direct-owner-schema-final.log` had1 failure/38 passes because owner function body changed while its exact golden admission hash was stale. This was change-caused transient failure, corrected by alignment and superseded by the final40-pass SQL suite and48-pass admission suite. It is not hidden or relabeled infrastructure. Earlier browser/async harness conflicts and inherited quality debt remain recorded by the coordinator.

The coordinator is consolidating these receipts into the final build report. Once that report reflects the final evidence, this review supports presenting the scoped diff for human approval; it does not authorize commit, push, PR, merge, migration, repair or deployment.
