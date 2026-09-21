# RQF-UX-002 — Browser Regression Plan

Status: PLAN
Date: 2026-09-21
Issue: #352

## Finding

## Slice 0 implementation status

Repository implementation exists in PR #356 (`test(ux): add browser journey harness`) and is green on HEAD `a3b71836522845fbcdeaf792a86e9243347be626`.

Verified evidence:
- Browser UX Pilot: PASS.
- 7 Playwright tests: 0 failures, 0 errors.
- Session guard plus authenticated Direction navigation.
- Keyboard focus/activation through the year selector, Update action and Reports link.
- Horizontal body overflow checks at 1440, 1280, 1024, 768 and 390 px.
- Screenshot/trace/server-log evidence path is active on failures.
- Existing required PR quality gate remains separate and passed.

This is repository-level test evidence only. PR #356 is not treated here as merged, deployed or business-accepted until those events occur.

The initial repository search did not find Playwright, Selenium or axe usage. Current UI tests are predominantly Python/source-contract tests.

This is a gap for usability regression, not a criticism of the existing CI: source-contract tests are valuable for route, policy and markup invariants.

## Proposal

Use Playwright for browser-level journeys. Add axe-core integration where practical for automated accessibility checks.

Do not gate the full existing suite immediately. Introduce in slices.

## Slice 0 — Harness

- Python or Node Playwright, whichever integrates cleanly with the current CI/runtime.
- deterministic test login/fixture mechanism;
- no production credentials;
- test database or isolated fixture data;
- screenshots/traces on failure;
- viewport matrix helpers;
- explicit cleanup/idempotency.

Acceptance:
- one authenticated read-only smoke;
- one form navigation smoke without committing financial state;
- CI artifact with screenshot/trace.

## Slice 1 — Navigation/read-only

Automate:
1. login → panel;
2. panel → informes;
3. panel → solicitudes;
4. authorized user → `/documentos/pendientes`;
5. Finance → `/admin/finanzas`;
6. Contabilidad → cleanup/COI;
7. Direction → executive dashboard within scope.

Check:
- page loads;
- expected heading;
- current nav state;
- no horizontal body overflow at standard viewports;
- keyboard focus visible on primary actions;
- no critical axe violations selected for gate.

## Slice 2 — Critical safe workflow fixtures

In isolated test data:
- draft request creation;
- draft report creation;
- rejection correction;
- approval decision;
- Control Presupuestal assignment.

Writes must use fixture records and existing canonical endpoints.

## Slice 3 — Finance state-machine workflows

With dedicated authorized fixtures:
- approved → Payment Run selection;
- close cutoff → `en_proceso_pago`;
- attach proof → `pagado`;
- assert cutoff alone does not show paid.

## Slice 4 — Accounting

- cleanup blocker → correction;
- COI preview;
- reconciliation candidate review;
- accepted-match semantics;
- CxC route once canonical path is reconciled.

## Viewports

At minimum:
- 1440 × 900
- 1280 × 800
- 1024 × 768
- 768 × 1024
- 390 × 844

Focused accessibility:
- keyboard-only;
- 200% browser zoom/manual UAT;
- touch targets on 390px;
- automated axe scan of representative pages.

## Assertions to prefer

Behavioral assertions:
- user-visible text;
- accessible roles/names;
- URL after action;
- business state returned/read;
- visible success/error feedback.

Avoid brittle assertions on:
- exact inline CSS strings;
- element nesting that does not affect behavior;
- pixel-perfect screenshots as sole acceptance.

Source-contract tests should continue to own low-level invariants where they are stronger.

## CI strategy

Initial:
- browser suite non-required while stabilizing fixtures.

Then:
- make read-only/navigation smoke required;
- promote high-risk journey tests after they are deterministic;
- keep full visual regression selective to avoid noisy gates.

## Required evidence per failed browser test

- journey id;
- effective profile;
- viewport;
- current URL;
- screenshot;
- trace;
- expected state;
- observed state.

## Security/authority

Browser tests must not create a parallel auth bypass in production code. Test authentication must be scoped to test runtime/fixtures.

Financial writes remain governed by the same canonical endpoints and authority checks as the application.
