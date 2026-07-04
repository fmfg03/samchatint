# SamChat Gastos Assistant Hardening RC1 Closeout

Date: 2026-07-04

## Branch

- Branch: `release/samchat-gastos-assistant-hardening-rc1`
- Base: `origin/p0/canonicalize-live-runtime-core-r2`
- Base HEAD: `249460309ec7d8acf11f9d143af1efefb3b20940`

## Purpose

Create a release-candidate line that contains both:

- Dashboard runtime hardening from `p0/canonicalize-live-runtime-core-r2`
- Assistant Reliability Stack v1 and runtime-clean hardening from `rqf-samchat-assistant-runtime-clean`

This was done by selective path checkout from runtime-clean, not by merging divergent branches.

## Applied

- Preserved `copa_telmex_dashboard.py` and its production `DATABASE_URL` guard from the dashboard line.
- Brought Assistant Reliability Stack v1 modules/tests from `origin/rqf-samchat-assistant-runtime-clean`.
- Brought runtime-clean Tocino webhook fail-closed behavior from `origin/rqf-samchat-assistant-runtime-clean`.
- Preserved dashboard tests already present on the dashboard line.
- Added empty `src/devnous/__init__.py` and `src/samchat/__init__.py` package markers in the RC so imports resolve to this worktree without pulling unrelated package exports from either divergent line.

## Validation Results

Compile checks:

- `python3 -m py_compile copa_telmex_dashboard.py`: passed
- `python3 -m py_compile src/samchat/assistant/router.py`: passed
- `python3 -m py_compile src/samchat/assistant/conversation_service.py`: passed
- `python3 -m py_compile src/samchat/assistant/action_router.py`: passed
- `python3 -m py_compile src/devnous/gastos/routes/webhook_handler.py`: passed

Targeted tests:

- `SESSION_SECRET_KEY=test-session-secret ./scripts/pytestw tests/unit/test_copa_telmex_dashboard_routes.py -k "database_url or healthz or readyz"`: 5 passed, 16 deselected
- `./scripts/pytestw tests/unit/test_assistant_action_router_contracts.py`: 15 passed
- `./scripts/pytestw tests/unit/test_assistant_document_runtime_smoke.py`: 6 passed
- `./scripts/pytestw tests/unit/test_assistant_request_router_integration.py`: 5 passed
- `./scripts/pytestw tests/unit/test_assistant_analyst_routing_integration.py`: 6 passed
- `./scripts/pytestw tests/unit/gastos/test_tocino_webhook_routes.py`: 2 passed

Diff hygiene:

- `git diff --check`: passed

Validation note:

- A local `.venv` symlink to `/root/samchat/.venv` was used only to run `scripts/pytestw`.
- `.venv` is untracked and must not be committed.

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

## Remaining Release Risks

- This RC reconciles the minimum release surfaces mechanically by path, not by full product acceptance testing.
- Before production deployment, run a service-level smoke on the actual `samchat-gastos.service` command and verify `/healthz` and `/readyz`.
- Confirm runtime safety flags before deployment:
  - `ASSISTANT_AGENT_RUNTIME_ENABLED=false`
  - `ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true`
  - `ASSISTANT_AGENT_WRITES_ENABLED=false`
  - `ASSISTANT_AGENT_SHADOW_ENABLED=false`

## External Effects

No DB migration, live DB access, provider call, OCR execution, external webhook call, main push, or force push was performed.
