# RQF_HARDENING_008 Reapply On Runtime Clean Closeout

Date: 2026-07-03

## Base HEAD

- Base branch: `origin/rqf-samchat-assistant-runtime-clean`
- Base HEAD: `333a2ff44e495b09c494afbde3a86167470057fa`
- Working branch: `rqf/hardening-001-005-runtime-clean`

## Commit

- Hardening commit: `14e923bbf3e9089515a9a97e89da69f0114f4cc2`
- Commit message: `chore: reapply targeted hardening on assistant runtime clean`

## Files Changed

- `artifacts/deployment/SAMCHAT_LIVE_RUNTIME_SOURCE_OF_TRUTH.md`
- `artifacts/repo_hygiene/SAMCHAT_GENERATED_AND_LEGACY_SURFACES_POLICY.md`
- `src/devnous/gastos/routes/webhook_handler.py`
- `src/samchat/assistant/router.py`
- `tests/unit/gastos/test_tocino_webhook_routes.py`
- `tests/unit/test_assistant_analyst_routing_integration.py`

## Scoped Changes Applied

- Added production fail-closed behavior for missing `TOCINO_WEBHOOK_SECRET` in `src/devnous/gastos/routes/webhook_handler.py`.
- Added minimal classifier report keywords for `riesgo`, `riesgos`, `contrato`, and `contratos` while preserving Assistant Reliability Stack v1 routing/service structure.
- Kept existing runtime-clean request router integration tests.
- Extended existing analyst routing integration coverage for `Qué riesgos ves en este contrato`, asserting analyst workbench handling without provider calls.
- Added Tocino webhook tests for production missing-secret rejection and configured-secret success.
- Added deployment source-of-truth and repo hygiene artifacts.

## Not Applicable On Runtime-Clean

- `copa_telmex_dashboard.py` is not present on this base, so the production `DATABASE_URL` dashboard startup guard was not applied here.
- `telegram_roster_ocr_bot.py` is present, but `git diff --check` did not require a whitespace fix; no change was made.
- No config guard tests for `copa_telmex_dashboard.py` were run because the dashboard file is absent on this base.

## Validation Results

Compile checks:

- `python3 -m py_compile src/samchat/assistant/router.py`: passed
- `python3 -m py_compile src/samchat/assistant/conversation_service.py`: passed
- `python3 -m py_compile src/samchat/assistant/action_router.py`: passed
- `python3 -m py_compile src/devnous/gastos/routes/webhook_handler.py`: passed

Assistant Reliability Stack v1 tests:

- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py`: 15 passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py`: 6 passed
- `./scripts/pytestw tests/unit/test_assistant_request_router_integration.py`: 5 passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_routing_integration.py`: 6 passed

Hardening tests:

- `./scripts/pytestw tests/unit/gastos/test_tocino_webhook_routes.py`: 2 passed

Diff hygiene:

- `git diff --check`: passed

Validation note: the clean worktree used the existing `/root/samchat/.venv` via a local `.venv` symlink because runtime-clean `scripts/pytestw` requires a worktree-local venv. The symlink was not committed.

## Forbidden/Frozen Path Check

No changes were reported for:

- `database/`
- `migrations/`
- `infrastructure/`
- `terraform/`
- `goal-fest-page/dist`
- `goal-fest-page/src/pages/Assistant.tsx`
- `src/devnous/gastos/models.py`
- `src/devnous/gastos/routes/admin_routes.py`
- `src/devnous/gastos/routes/user_routes.py`

## Assistant Reliability Stack V1 Preservation

Assistant Reliability Stack v1 remains authoritative on this branch. The reapply did not replace or broadly merge assistant modules from the dashboard hardening branch. Existing runtime-clean tests for action router contracts, document runtime smoke, deterministic request routing, and analyst routing all pass after the scoped changes.

## Push Result

- Command: `git push origin HEAD:rqf/hardening-001-005-runtime-clean`
- Result: passed
- Remote branch created: `origin/rqf/hardening-001-005-runtime-clean`
- Remote PR URL suggested: `https://github.com/fmfg03/samchatint/pull/new/rqf/hardening-001-005-runtime-clean`

## External Effects

No main push, no force push, no DB migration, no live DB access, no provider call, no OCR execution, no external webhook call, and no frozen UI changes were performed.
