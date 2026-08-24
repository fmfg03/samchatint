# Branch risk audit closeout — 2026-08-24

This closeout records the one-by-one audit of branches that were not safe to merge wholesale after the branch prune pass. The rule for this pass was conservative: rescue only small, current, testable changes; do not merge old branches that would overwrite newer assistant, owner-pack, finance, or budget-control work.

## Baseline

- Working branch: `codex/roadmap-finance-assistant-20260821`
- Main baseline merged into branch before this pass: `6aa7415c1` (`origin/main` at audit start)
- User files intentionally left untouched:
  - `2.1. Catálogo Contable.xls`
  - `Egresos.xlsx`
  - `Estrategia de Autorización.xlsx`
  - `Grabación de pantalla 2026-07-31 164539.mp4`
  - `Ingresos.xlsx`
  - `branch-prune-manifest-20260730T015022Z.tsv`

## Rescued safely

### `origin/codex/rqf-053-ui-artifact-verification`

- Source commit: `d3937358752cf0e533d9a47d900d9da85634b436`
- Rescued as: `720c2d190 assistant: verify 053 ui artifacts`
- Result: kept the assistant UI artifact safety tests without merging the old branch.
- Verification: `tests/unit/test_assistant_ui_artifact_safety.py` passed.

### `origin/codex/hotfix-contabilidad-white-screen`

- Source commit: `b0c0f66437ee270794b0136a35494a5fb6a4df6a`
- Rescued as: `70a2b82a9 contabilidad: expose bulk account catalog controls`
- Result: kept the missing regression test for bulk account catalog controls. The production UI controls were already present in the current branch.
- Verification: `tests/unit/gastos/test_cuentas_contables_bulk_catalog.py` passed.

### Current budget-control behavior test alignment

- Commit: `b608f4484 test: align informe close with budget control gate`
- Reason: an old beneficiary-selector test expected an informe close to go directly to `enviado`. Current product behavior correctly sends informes without a budget concept to `control_presupuestal` first. The test now covers both paths:
  - no budget concept → `control_presupuestal`
  - budget concept assigned → `enviado`

## Audited and skipped / superseded

### `d6d07443c gastos: add filters to pending approvals`

- Attempted cherry-pick: conflicted in `src/devnous/gastos/routes/user_routes.py`.
- Current status: behavior is already present/evolved in current code (`Buscar aprobaciones`, filters, and `Torneo` column are in the live route code).
- Decision: skip as superseded; do not merge the old branch.

### `74361cfe8 gastos: add no white screen fallback`

- Attempted cherry-pick: empty.
- Current status: fallback behavior already exists in current code.
- Decision: skip as already applied/superseded.

### `f4acaee04 operaciones: degrade console data failures`

- Attempted cherry-pick: empty.
- Current status: already applied/superseded.
- Decision: skip.

### `ed665a793 gastos: rollback optional cleanup precedent failures`

- Attempted cherry-pick: conflicted in accounting cleanup service and tests.
- Current status: the current branch has stronger/evolved cleanup degradation logic, including `preview_degraded`, nested transaction isolation, and historical precedent tests.
- Decision: skip as superseded by stronger current implementation.

### `32e29efc6 gastos: degrade cleanup preview failures`

- Not cherry-picked separately because the current implementation already carries the evolved degraded-preview/nested-isolation path.
- Decision: skip as superseded.

### `fda9490d1 gastos: clarify informe beneficiary mode toggles`

- Attempted cherry-pick: conflicted in `tests/unit/gastos/test_informe_beneficiary_selector.py`.
- Current status: current tests now pass against the evolved Control Presupuestal flow and beneficiary-selector behavior.
- Decision: skip as superseded; keep current tests, not the old branch shape.

### `4d2377b02 gastos: align labels for descripcion and concepto`

- Attempted cherry-pick: conflicted in `admin_routes.py`, `user_routes.py`, and `documento_service.py`.
- Finding: useful product intent, but unsafe as a cherry-pick because the surrounding UI and validation changed substantially. It also contains a bad Spanish string (`El descripción de pago es requerida.`) that should not be reintroduced.
- Decision: do not merge directly. Reimplement as a fresh scoped UI-label story if needed, using the current routes as source of truth.

## Large branch kept out of the merge path

### `codex/c4-presupuestos-version-line-owner`

- Source commit: `d2e2d48fe refactor: move presupuestos version line owner`
- Scope: large route ownership refactor (~900 touched lines) across budget admin routes, docs, and tests.
- Finding: the current code still intentionally carries bridge/legacy route coverage and safety tests. Full merge would be a structural refactor, not a cleanup rescue.
- Decision: keep as technical-debt reference only. Do not merge in the current consolidation pass.

### `codex/rqf-assistant-052-specialist-agents-main`

- Finding from earlier audit: current assistant code already has the mature specialist benchmark/registry/read-adapter path. Full merge from this historical branch would risk deleting newer assistant and owner-pack work.
- Decision: keep as historical reference only; do not merge wholesale.

## Verification run in this pass

- `tests/unit/test_assistant_ui_artifact_safety.py` → passed
- `tests/unit/gastos/test_cuentas_contables_bulk_catalog.py` → passed
- `tests/unit/gastos/test_informe_beneficiary_selector.py` → passed
- `scripts/ci/check-accepted-regressions.py` → passed

## Conclusion

The unsafe branches were reviewed one by one. Two small changes were rescued, one current behavior test was corrected, and the remaining branch content was either already superseded or too old/risky to merge safely. The next assistant/owner work should proceed from the consolidated branch, not from historical branches.
