# RQF-UX-002 — Friction Scorecard Baseline

Status: STRUCTURAL_BASELINE_ONLY
Date: 2026-09-21
Issue: #352
Baseline: `main@361ad7a2`

## Important limitation

The repository does not contain measured task times/clicks/backtracks for the critical journeys. Therefore this scorecard does **not** invent numeric usability scores.

Use:
- structural signals from repository evidence;
- `NOT_MEASURED` for behavioral metrics;
- provisional priority only.

## Dimensions

For every journey capture:

| Dimension | Measurement |
| --- | --- |
| Task success | PASS / PASS_WITH_HELP / FAIL |
| Time | seconds |
| Clicks/taps | count |
| Screens/routes | count |
| Backtracks | count |
| Wrong first click | yes/no |
| Help requests | count |
| Validation errors | count |
| Business errors | count |
| Terms requiring explanation | count/list |
| Next-step comprehension | clear / partial / wrong |
| State comprehension | clear / partial / wrong |
| Accessibility | keyboard/zoom/mobile result |

## Structural risk markers

- `MULTI_HUB`: task can plausibly start in several domain hubs.
- `MULTI_QUEUE`: same object appears in multiple workflow queues.
- `SPLIT_ROUTE_FAMILY`: list/create/detail span different route families.
- `HIGH_RISK_DECISION`: incorrect action has financial/authority impact.
- `INTERNAL_TERMINOLOGY`: task exposes system/accounting terminology.
- `UAT_PENDING`: repository says business UAT is pending.
- `LEGACY_OVERLAP`: canonical and legacy/bridge routes coexist.
- `DUAL_SURFACE`: two valid surfaces expose the same business concept for different tasks.
- `NO_BROWSER_GATE`: no automated browser journey coverage.

## Journey baseline

| Journey | Structural markers | Behavioral baseline | Provisional priority |
| --- | --- | --- | --- |
| J01 Transfer request | SPLIT_ROUTE_FAMILY, INTERNAL_TERMINOLOGY | NOT_MEASURED | P1 |
| J02 Advance | SPLIT_ROUTE_FAMILY | NOT_MEASURED | P2 |
| J03 Expense report | MULTI_QUEUE | NOT_MEASURED | P1 |
| J04 Expense/CFDI capture | INTERNAL_TERMINOLOGY, UAT_PENDING | NOT_MEASURED | P1 |
| J05 Correct rejection | MULTI_QUEUE | NOT_MEASURED | P1 |
| J06 Understand status | MULTI_QUEUE | NOT_MEASURED | P1 |
| J07 Approve/reject | HIGH_RISK_DECISION, MULTI_QUEUE | prior UI defect class documented; new metrics NOT_MEASURED | P1 |
| J08 Approval history | MULTI_QUEUE | NOT_MEASURED | P2 |
| J09 Budget assignment | HIGH_RISK_DECISION, INTERNAL_TERMINOLOGY | NOT_MEASURED | P1 |
| J10 Ready for payment | MULTI_HUB, MULTI_QUEUE, INTERNAL_TERMINOLOGY | NOT_MEASURED | P1 |
| J11 Payment Run | HIGH_RISK_DECISION, MULTI_HUB, UAT_PENDING | NOT_MEASURED | P1 |
| J12 Payment proof | HIGH_RISK_DECISION, MULTI_QUEUE | NOT_MEASURED | P1 |
| J13 Accounting cleanup | MULTI_HUB, INTERNAL_TERMINOLOGY, UAT_PENDING | NOT_MEASURED | P1 |
| J14 COI review/export | MULTI_HUB, INTERNAL_TERMINOLOGY, UAT_PENDING | NOT_MEASURED | P2 |
| J15 Bank reconciliation | HIGH_RISK_DECISION, INTERNAL_TERMINOLOGY, UAT_PENDING | NOT_MEASURED | P1 |
| J16 CxC | DUAL_SURFACE, MULTI_HUB, UAT_PENDING | NOT_MEASURED | P1 |
| J17 Budgets | LEGACY_OVERLAP, INTERNAL_TERMINOLOGY | NOT_MEASURED | P2 |
| J18 Direction scan | MULTI_HUB, scope constrained | NOT_MEASURED | P2 |
| J19 Create support ticket | none material from repo sweep | NOT_MEASURED | P3 |
| J20 Support triage | INTERNAL_TERMINOLOGY | NOT_MEASURED | P2 |

## Priority rules

### UX-P0
Use only when UAT or incident evidence shows:
- user cannot complete critical task;
- wrong action can occur without adequate guard;
- user can misinterpret financial completion;
- accessibility blocks required work.

No P0 is declared from repository structure alone.

### UX-P1
Critical/frequent journey with structural risk or known prior usability defect.

### UX-P2
Material cognitive load, inconsistency or expert-only complexity.

### UX-P3
Polish or lower-frequency improvement.

## Before/after comparison template

For each implemented UX change:

| Metric | Before | After | Delta | Evidence |
| --- | ---: | ---: | ---: | --- |
| Task success | | | | |
| Median time | | | | |
| Median clicks | | | | |
| Backtracks | | | | |
| Help requests | | | | |
| Wrong first click | | | | |
| Next-step comprehension | | | | |

Do not claim improvement unless the task and data are comparable.
