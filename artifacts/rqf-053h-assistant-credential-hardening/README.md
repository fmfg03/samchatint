# RQF-053H Assistant Credential Surface Hardening

Status: DEPLOYED_STATIC_ASSETS
Date: 2026-08-31

## Scope

Hardened the Assistant UI so browser-provided provider credentials are not read,
persisted, or forwarded from `/assistant`.

This slice only changes the frontend artifact and deployed static assets. It
does not remove the backend `X-OpenAI-API-Key` contract, change provider
execution, modify database state, or enable writes.

## Runtime Target

- Backend service: `samchat-gastos.service`
- Frontend source:
  `/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`
- Served static dist: `/srv/samchat/current/goal-fest-page/dist`

## Behavior

- Removes the legacy browser storage key
  `samchat_assistant_openai_api_key` on Assistant load.
- Detects `openai_api_key` and `openai_key` URL parameters with `has(...)`
  only; it does not read their values.
- Deletes those URL parameters with `delete(...)` and replaces browser history.
- Shows the safe message:
  `Las API keys no se aceptan por URL. Usa credenciales server-side configuradas.`
- Does not define `OPENAI_KEY_STORAGE_KEY`.
- Does not keep `sessionOpenAiKey` state.
- Does not attach `X-OpenAI-API-Key` in `api()` or `apiDownload()`.
- Validates persisted Assistant mode with an allowlist and falls back to
  `ahorro`.

## Predeploy Evidence

- Asset listing: `predeploy-assets.txt`
- Assistant asset before deploy: `Assistant-Cj-wzq_B.js`
- Asset count before deploy: `101`
- `/healthz`: healthy at `2026-08-31T00:49:27.159177+00:00`
- `/readyz`: healthy at `2026-08-31T00:49:27.253258+00:00`
- Rollback backup created:
  `/srv/samchat/current/goal-fest-page/dist.rollback-20260831-rqf053h-credentials`

## Build And Deploy

Artifact copied from:
`/root/samchat/artifacts/rqf-053h-assistant-ui-revamp/Assistant.tsx`

Artifact copied to:
`/srv/samchat/archive/projects/goal-fest-page/src/pages/Assistant.tsx`

Build command:

```bash
npm run build
```

Build result:

- Success
- Generated assistant asset: `Assistant-DM-K94_6.js`
- Reported bundle size: `60.36 kB`, gzip `14.33 kB`

Deploy command:

```bash
rsync -a --delete /srv/samchat/archive/projects/goal-fest-page/dist/ /srv/samchat/current/goal-fest-page/dist/
```

## Postdeploy Evidence

- Asset listing: `postdeploy-assets.txt`
- Assistant asset after deploy: `Assistant-DM-K94_6.js`
- Served assistant asset size: `60451`
- Asset count after deploy: `101`
- `/healthz`: healthy at `2026-08-31T00:50:04.467968+00:00`
- `/readyz`: healthy at `2026-08-31T00:50:04.982247+00:00`

Verified positive bundle markers:

- `Las API keys no se aceptan por URL`
- `Cargando historial`
- `No se pudo cargar el panel ejecutivo`
- `external_session_id`

Verified absent from the served assistant asset:

- `X-OpenAI-API-Key`
- `sessionOpenAiKey`
- `setSessionOpenAiKey`
- `OPENAI_KEY_STORAGE_KEY`

Expected remaining literals:

- `samchat_assistant_openai_api_key`
- `openai_api_key`
- `openai_key`

Those literals remain only to remove legacy browser storage and delete unsafe
URL parameters.

## Rollback

Use this command to restore the predeploy dist:

```bash
rsync -a --delete /srv/samchat/current/goal-fest-page/dist.rollback-20260831-rqf053h-credentials/ /srv/samchat/current/goal-fest-page/dist/
```

After rollback, verify `/healthz`, `/readyz`, and that the served assistant
asset returns to the predeploy asset listed in `predeploy-assets.txt`.

## Known Follow-Up

The backend still accepts `X-OpenAI-API-Key` on Assistant endpoints. That is no
longer sent by the Assistant UI after this slice, but backend header acceptance
should be evaluated as a separate hardening change before making stronger
credential-boundary claims.
