# Dashboard Runtime Database URL Guard

Date: 2026-07-04

## Scope

This branch applies the remaining `RQF_HARDENING_001_005` dashboard-only guard to the runtime branch that tracks `copa_telmex_dashboard.py`.

## Base

- Base branch: `origin/p0/canonicalize-live-runtime-core-r2`
- Base HEAD: `c22c9b2f886473c07f91d8e625d4c40da0d33239`

## Applied

- Added explicit production-mode detection using `SAMCHAT_ENV`, `ENVIRONMENT`, `APP_ENV`, or `FASTAPI_ENV` values `production`, `prod`, or `live`.
- Added `DATABASE_URL` fail-fast behavior only when production mode is explicit.
- Preserved the existing local/dev fallback database URL outside explicit production mode.
- Added focused tests for production missing `DATABASE_URL` and dev fallback behavior.

## Not Changed

- No Assistant Reliability Stack v1 files were touched.
- No `runtime-clean` branch changes were included.
- No DB schema, migrations, provider calls, OCR execution, external webhook calls, frozen UI, or main branch changes were made.

## Validation

- `python3 -m py_compile copa_telmex_dashboard.py`: passed
- `PYTHONPATH=/tmp/samchat-dashboard-db-guard/src SESSION_SECRET_KEY=test-session-secret /root/samchat/.venv/bin/python -m pytest tests/unit/test_copa_telmex_dashboard_routes.py -k "database_url or healthz or readyz"`: 5 passed, 16 deselected
- `git diff --check`: passed

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
- `src/samchat/assistant/`

## Branch

- Branch: `rqf/hardening-dashboard-database-url-guard`
