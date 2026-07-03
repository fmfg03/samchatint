# SamChat Live Runtime Source Of Truth

Created for `RQF_HARDENING_001_005` on 2026-07-03.

## Observed Checkout

- Working directory: `/root/samchat`
- Clean-pack base used for this branch: `c22c9b2f8`
- Note: `main` is not automatically the live source of truth. The live identity must be confirmed from the deployed checkout, service unit, and process command.

## Live Runtime Identity

- Live service name: `samchat-gastos.service`
- Runtime entrypoint: `uvicorn copa_telmex_dashboard:app --host 127.0.0.1 --port 8000`
- Live web app module: `copa_telmex_dashboard.py`
- Secondary repo surfaces such as `src/devnous/api.py`, `src/samchat/main.py`, and `mcp_platform_launcher.py` are not interchangeable with the live gastos dashboard runtime.

## Assistant Safety Flags

Expected current safety posture:

- `ASSISTANT_AGENT_RUNTIME_ENABLED=false`
- `ASSISTANT_AGENT_RUNTIME_READONLY_ONLY=true`
- `ASSISTANT_AGENT_WRITES_ENABLED=false`
- `ASSISTANT_AGENT_SHADOW_ENABLED=false`

These values document the expected deployed safety mode. Do not print or persist secret values while verifying runtime environment.

## Health Checks

Run against localhost from the deployment host:

```bash
curl -sS http://127.0.0.1:8000/healthz
curl -sS http://127.0.0.1:8000/readyz
```

If the service is exposed through a reverse proxy, verify proxy health separately without assuming it changes the Python runtime identity.

## Frontend Artifact Note

Nested frontend source-to-dist mapping still requires an explicit build and deploy contract. In particular, do not delete or regenerate nested `dist` artifacts such as `goal-fest-page/dist` or `copatelmex/dist` until the live frontend dependency map is proven.
