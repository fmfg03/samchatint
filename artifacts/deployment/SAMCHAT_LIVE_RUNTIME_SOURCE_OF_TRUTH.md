# SamChat Live Runtime Source Of Truth

Created for `RQF_HARDENING_008_REAPPLY_ON_RUNTIME_CLEAN` on 2026-07-03.

## Runtime-Clean Base

- Base branch: `origin/rqf-samchat-assistant-runtime-clean`
- Observed base HEAD: `333a2ff44e495b09c494afbde3a86167470057fa`
- This branch preserves Assistant Reliability Stack v1 as authoritative.

## Live Runtime Identity

Known deployment identity from the hardening audit:

- Live service name: `samchat-gastos.service`
- Working directory: `/root/samchat`
- Runtime entrypoint: `uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000`

`copa_telmex_dashboard.py` is not tracked on this runtime-clean base, so the dashboard `DATABASE_URL` startup guard is not applied in this branch. Do not assume `main` or this assistant runtime branch is automatically the live dashboard source of truth.

## Assistant Safety Flags

Expected current safety posture:

- `ASSISTANT_AGENT_RUNTIME_ENABLED=false`
- `ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true`
- `ASSISTANT_AGENT_WRITES_ENABLED=false`
- `ASSISTANT_AGENT_SHADOW_ENABLED=false`

Do not print or persist secret values while verifying runtime environment.

## Health Checks

If verifying the deployed dashboard host, use localhost checks against the actual service process:

```bash
curl -sS http://127.0.0.1:8000/healthz
curl -sS http://127.0.0.1:8000/readyz
```

## Frontend Artifact Note

Nested frontend source-to-dist mapping still requires an explicit build and deploy contract. Do not delete or regenerate nested `dist` artifacts until the live frontend dependency map is proven.
