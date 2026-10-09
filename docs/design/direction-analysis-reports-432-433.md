# Direction: analysis and council reports (#432, #433)

Implementation date: 2026-09-30. Base: main `11ac5299be0d4a69b5ab10631d947e6df7b3ad1f` (merged #431 / PR #435).

## Behavior

Sam answers with conclusion, facts/evidence, interpretation, explicitly unverified hypotheses, recommendations with alternatives and risks, and next step. Excesses are ordered by amount; annual forecast, actual overrun and concept composition remain distinct and are never summed as independent losses. A variation is not an accredited cause.

The canonical budget snapshot supplies the complete concept decomposition only for executive reads; existing callers retain their six-item breakdown. Direction groups by canonical budget concept ID, keeping renamed and identically named concepts distinct. It only exposes concepts when their amounts reconcile with the visible budget and interval expense. The immediately preceding equal-duration interval is read within the same edition and scope; crossing an edition or comparing stock/annual indicators produces a visible gap.

Three deterministic scenarios cover expense reduction, accepted collection acceleration, and deferral of currently visible scheduled obligations. Percentages/days are explicit assumptions. Expense reduction distinguishes observed expense from the remaining mechanical forecast; the latter is not proof of discretionary or cancellable spending. Payment deferral requires complete amount/date evidence and does not cancel debt or prove bank availability. Follow-up percentage/day corrections reuse signed assumptions. No financial operations are modified or approved.

The board and Sam download PDF/XLSX from the same signed snapshot. PDF contains conclusion, indicators, annual budget versus interval expense graphs, risks, alternatives, scenario assumptions and limits, missing data, definitions and sources. Excel preserves numeric facts, blanks for missing data, sources, definitions, per-source cuts and supporting rows; scenarios have a separate sheet and auditable formulas with cached signed results. User labels cannot create spreadsheet formulas. Published legacy reports render whitelisted business facts instead of technical JSON.

## Authority and cut

The new POST `/direccion/reportes/exportar/{pdf|xlsx}` shares the conversation's session CSRF check, actor-bound 15-minute signed snapshot and live scope/domain permission revalidation. An optional signed analysis receipt binds the actor and snapshot and prevents client-authored amounts or conclusions. Tokens are not placed in URLs. Responses use no-store, attachment disposition and a matching snapshot header. Existing Direction position/portfolio scope, superadmin supervision and explicit denials remain the owners. No global cross-entity access, public sharing, external sending, schema installation or financial mutation is introduced.

The cut is a signed collection of independent source reads, not a cross-store atomic transaction. Sources may have individual cuts. The report carries that limitation, coverage and pending business acceptance. Observed expense, documented paid state, pending collections and reconciled bank cash remain separate.

## Canon unchanged

Canon unchanged: #432/#433 implement the already-authorized read, investigate, recommend, scenario and report capabilities of scoped Direction. They add no decision/write authority, new source of financial truth, schema or cross-entity boundary. Product, engineering and sweep canon hashes were verified against the convergence register; canon edits are unnecessary. Deployment and owners' UAT are not implied by these tests or a merge.

## Verification and rollout

See `docs/evidence/direction-432-433/README.md`. Synthetic fixture evidence verifies arithmetic, source/scope boundaries, signed continuity, download parity and browser usability; it is not real business UAT. The PDF was rendered and all three pages inspected for legibility, wrapping and clipping. Existing canonical XLSX/PDF export infrastructure is reused, with ReportLab declared as a runtime dependency.

After CI and review, merge normally; deploy the exact approved SHA through the existing release process, then have Direction/Finance/Treasury validate real scope, amounts, definitions, scenarios and council downloads. Keep #432/#433 acceptance pending until that validation is recorded. No production deploy or business acceptance was performed here.
