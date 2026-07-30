# RQF-058 — Sprint Close Candidate

Status: STORY_READY_APPROVED
Date: 2026-07-30
Branch: `sprint-rqf-056-beneficiary-ops`
Owner intent: prepare the current sprint branch for push, PR, review and final merge without expanding product scope.

## Objective

Convert the current sprint branch into a close candidate: verify that the implemented customer Excel changes are coherent, tested, documented and safe to present for review, while explicitly separating external dependencies and post-sprint scope.

## Scope included

This story closes the implementation sprint around:

- third-party beneficiaries in Anticipos and Informes;
- regional operator beneficiary eligibility;
- draft cancellation/cleanup;
- materiality preview before save;
- CFDI XML/PDF total handling;
- budget visibility/mutation policy;
- Telegram project/phase context;
- employee reimbursement semantics;
- Telegram notification audit and Odilon/Benjamin timing;
- provider search in Solicitudes consultation;
- No Deducibles accounting rule;
- SAT massive download operational readiness;
- authorization strategy matrix/profile/advisory/enforcement-readiness.

## Explicitly out of scope for this close candidate

These are not blockers for the sprint branch unless the customer reclassifies them:

- live SAT download witness before FIEL/e.firma is provided and configured;
- installing production crontab before `SAT_SYNC_SECRET` and FIEL are present;
- hard activation of `SAMCHAT_AUTHORIZATION_STRATEGY_ENFORCEMENT`;
- full sequential first/second approval state machine;
- the parked Tocino retry/status/Telegram hardening beyond existing partial classification;
- the broader Operations end-to-end audit and Supabase migration plan.

## Acceptance criteria

### Repository sanity

- Branch is `sprint-rqf-056-beneficiary-ops`.
- No unexpected modified/staged files.
- Known untracked artifacts are documented and not accidentally committed:
  - `2.1. Catálogo Contable.xls`
  - `Estrategia de Autorización.xlsx`
  - `branch-prune-manifest-20260730T015022Z.tsv`
- No secrets, FIEL files, private keys or generated customer payloads are committed.

### Automated verification

Run and record:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m py_compile \
  src/devnous/gastos/routes/user_routes.py \
  src/devnous/gastos/routes/admin_routes.py \
  src/devnous/gastos/routes/webhook_handler.py \
  src/devnous/gastos/services/documento_workflow_service.py \
  src/devnous/gastos/services/authorization_profile_service.py \
  src/devnous/gastos/services/authorization_strategy_service.py \
  src/devnous/sat/sat_sync_service.py

PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/gastos/test_authorization_profile_board.py \
  tests/unit/gastos/test_authorization_strategy_service.py \
  tests/unit/sat/test_sat_sync_operational_contract.py \
  -q

git diff --check
```

If time allows before PR, also run the broader gastos regression subset covering beneficiary selectors, CFDI totals, budgets, materialities, no-deducibles and Telegram message rendering.

### Functional evidence to preserve in PR description

- Anticipos: authorized users can select employee/operator beneficiary; unauthorized users stay self-scoped.
- Informes: authorized users can select employee/operator beneficiary; ownership and approval route remain requester-scoped.
- Bank accounts belong to selected beneficiary.
- Drafts can be cancelled only under allowed draft/incomplete conditions.
- Materialidades show filename/thumbnail/remove before saving.
- CFDI quick capture honors XML `Total` as authoritative.
- Budgets are visible only to superadmin/directors/Alicia and editable only by superadmin.
- Telegram approval messages include proyecto and etapa.
- Reimbursements to employees do not label the employee as proveedor.
- Provider filter appears in Solicitudes consultation.
- No-CFDI requests/lines map to project-specific Gastos No Deducibles.
- SAT connector is ready for received/issued sync at 09:00 and 23:00 after credentials.
- Authorization strategy UI/evidence/warnings/enforcement switch are present; enforcement remains off by default.

## Close procedure

1. Run repository sanity and automated verification.
2. Update this story with the exact verification result and commit hash.
3. Push branch.
4. Open PR as sprint close candidate, not final release.
5. PR description must include:
   - implemented scope;
   - tests run;
   - external pending items;
   - known untracked artifacts intentionally excluded;
   - deployment switches not enabled by default.
6. Review diff for secrets and accidental customer artifacts.
7. Merge only after review approval.

## Non-claims

- This story does not certify production release acceptance.
- This story does not replace the future formal QA/UAT protocol.
- This story does not claim live SAT success without FIEL/e.firma.
- This story does not claim the full Operations/Supabase migration audit is complete.

## Verification result

Status: LOCAL_GATE_PASSED
Verified at: 2026-07-30
Verified head before evidence commit: `3e2c66eea`

Commands executed:

```bash
PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m py_compile \
  src/devnous/gastos/routes/user_routes.py \
  src/devnous/gastos/routes/admin_routes.py \
  src/devnous/gastos/routes/webhook_handler.py \
  src/devnous/gastos/services/documento_workflow_service.py \
  src/devnous/gastos/services/authorization_profile_service.py \
  src/devnous/gastos/services/authorization_strategy_service.py \
  src/devnous/sat/sat_sync_service.py

PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/gastos/test_authorization_profile_board.py \
  tests/unit/gastos/test_authorization_strategy_service.py \
  tests/unit/sat/test_sat_sync_operational_contract.py \
  -q
# Result: 29 passed, 1 warning

PYTHONPATH=src /srv/samchat/venvs/baseline-db08f745e8da7a82/bin/python -m pytest \
  tests/unit/gastos/test_beneficiary_draft_controls.py \
  tests/unit/gastos/test_informe_beneficiary_selector.py \
  tests/unit/gastos/test_solicitar_anticipo_route.py \
  tests/unit/gastos/test_solicitud_terceros_routes.py \
  tests/unit/gastos/test_cfdi_quick_expense_totals.py \
  tests/unit/gastos/test_admin_budget_route_safety.py \
  tests/unit/gastos/test_budget_line_update_authorization.py \
  tests/unit/gastos/test_no_deducible_project_rules.py \
  tests/unit/gastos/test_reimbursement_document_semantics.py \
  tests/unit/gastos/test_employee_provider_registry.py \
  tests/unit/gastos/test_authorization_profile_board.py \
  tests/unit/gastos/test_authorization_strategy_service.py \
  tests/unit/sat/test_sat_sync_operational_contract.py \
  -q
# Result: 134 passed, 4 warnings

git diff --check
# Result: passed
```

Repository sanity:

- Branch confirmed: `sprint-rqf-056-beneficiary-ops`.
- GitHub CLI available and authenticated as `fmfg03`.
- Remote confirmed: `https://github.com/fmfg03/samchatint`.
- Only known untracked artifacts remain untracked and intentionally excluded.

